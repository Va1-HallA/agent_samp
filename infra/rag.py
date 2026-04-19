"""RAG retrieval stack on AWS.

    Chunker -> BedrockEmbedder -> OpenSearchHybridRetriever
                                      (knn + match in one index, RRF fused)
                                             -> KnowledgeBase

Why a single OpenSearch index instead of Milvus + Elasticsearch?
    - OpenSearch supports knn_vector and text fields in the same mapping, so
      dense + sparse retrieval hit the same shards and the same documents. A
      single cluster replaces two, cutting idle cost roughly in half.
    - Serverless OpenSearch is an option but kept as a drop-in (the client
      construction is identical; only the service code in SigV4 changes).

Auth: SigV4 via ``requests-aws4auth`` + ``opensearch-py``'s RequestsHttpConnection.
Credentials come from the standard boto3 chain — the ECS task role on AWS,
local ~/.aws/credentials in dev.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, helpers
from requests_aws4auth import AWS4Auth

import config
from core.llm_backend import BedrockBackend, LLMBackend, LLMError

logger = logging.getLogger(__name__)


# ============================================================
# Chunker
# ============================================================

class Chunker:
    """Sliding-window chunker with snap-to-separator and overlap."""

    def __init__(self, chunk_size: int | None = None, overlap: int | None = None):
        self.chunk_size = chunk_size or config.CHUNK_SIZE
        self.overlap = overlap or config.CHUNK_OVERLAP
        if self.chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {self.chunk_size}")
        if not 0 <= self.overlap < self.chunk_size:
            raise ValueError(
                f"overlap must be in [0, chunk_size), got overlap={self.overlap} chunk_size={self.chunk_size}"
            )

    def split(self, source: str, content: str) -> list[dict]:
        chunks: list[dict] = []
        start = 0
        n = len(content)

        while start < n:
            end = start + self.chunk_size
            slice_text = content[start:end]

            if end < n:
                for sep in [".", "!", "?", "\n", ";", ","]:
                    last_sep = slice_text.rfind(sep)
                    if last_sep > len(slice_text) * 0.5:
                        slice_text = slice_text[: last_sep + 1]
                        break

            if slice_text.strip():
                chunks.append({
                    "source": source,
                    "text": slice_text.strip(),
                    "char_start": start,
                })

            advance = max(1, len(slice_text) - self.overlap)
            start += advance

        return chunks


# ============================================================
# Embedder (Bedrock Titan Embed v2)
# ============================================================

class BedrockEmbedder:
    """Titan Embed v2 produces L2-normalised vectors of configurable dim."""

    def __init__(self, llm: LLMBackend | None = None, model: str | None = None, dim: int | None = None):
        self.llm = llm or BedrockBackend(region=config.AWS_REGION)
        self.model = model or config.BEDROCK_EMBEDDING_MODEL_ID
        self.dim = dim or config.EMBEDDING_DIM

    def encode_query(self, text: str) -> list[float]:
        return self.llm.embed(model=self.model, text=text)

    def encode_docs(self, texts: Iterable[str]) -> list[list[float]]:
        # Titan Embed has no batch endpoint; loop sequentially. Rate is typically
        # not the bottleneck for the offline build.
        out: list[list[float]] = []
        for t in texts:
            try:
                out.append(self.llm.embed(model=self.model, text=t))
            except LLMError:
                logger.exception("embed failed for chunk of length %d", len(t))
                out.append([0.0] * self.dim)
        return out


# ============================================================
# OpenSearch hybrid retriever
# ============================================================

def _default_client(endpoint: str, region: str) -> OpenSearch:
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError(
            "no AWS credentials available; configure an ECS task role or "
            "aws configure locally"
        )
    awsauth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        "es",
        session_token=credentials.token,
    )
    return OpenSearch(
        hosts=[{"host": endpoint, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )


class OpenSearchHybridRetriever:
    """Single index holding both the text and the knn_vector for each chunk."""

    def __init__(
        self,
        endpoint: str | None = None,
        index_name: str | None = None,
        region: str | None = None,
        dim: int | None = None,
        client: OpenSearch | None = None,
    ):
        self.endpoint = endpoint or config.OPENSEARCH_ENDPOINT
        self.index_name = index_name or config.OPENSEARCH_INDEX
        self.region = region or config.AWS_REGION
        self.dim = dim or config.EMBEDDING_DIM
        if client is not None:
            self.client = client
        else:
            if not self.endpoint:
                raise RuntimeError("OPENSEARCH_ENDPOINT is not configured")
            self.client = _default_client(self.endpoint, self.region)

    # ----- index lifecycle -----

    def _mapping(self) -> dict[str, Any]:
        return {
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "knn": True,
                },
            },
            "mappings": {
                "properties": {
                    "text": {"type": "text"},
                    "source": {"type": "keyword"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": self.dim,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                            "parameters": {"ef_construction": 128, "m": 24},
                        },
                    },
                }
            },
        }

    def ensure_index(self) -> None:
        if not self.client.indices.exists(index=self.index_name):
            self.client.indices.create(index=self.index_name, body=self._mapping())

    def reset(self) -> None:
        if self.client.indices.exists(index=self.index_name):
            self.client.indices.delete(index=self.index_name)
        self.ensure_index()

    # ----- write -----

    def bulk_insert(self, docs: list[dict]) -> None:
        """docs: [{text, source, embedding}]."""
        self.ensure_index()
        actions = (
            {
                "_index": self.index_name,
                "_source": {
                    "text": d["text"],
                    "source": d["source"],
                    "embedding": d["embedding"],
                },
            }
            for d in docs
        )
        helpers.bulk(self.client, actions, refresh="wait_for")

    # ----- read -----

    def dense_search(self, query_vec: list[float], top_k: int = 20) -> list[dict]:
        if not self.client.indices.exists(index=self.index_name):
            return []
        body = {
            "size": top_k,
            "query": {
                "knn": {
                    "embedding": {"vector": query_vec, "k": top_k}
                }
            },
            "_source": ["text", "source"],
        }
        resp = self.client.search(index=self.index_name, body=body)
        return self._hits(resp)

    def sparse_search(self, query: str, top_k: int = 20) -> list[dict]:
        if not self.client.indices.exists(index=self.index_name):
            return []
        body = {
            "size": top_k,
            "query": {"match": {"text": {"query": query, "operator": "or"}}},
            "_source": ["text", "source"],
        }
        resp = self.client.search(index=self.index_name, body=body)
        return self._hits(resp)

    @staticmethod
    def _hits(resp: dict) -> list[dict]:
        out = []
        for hit in resp.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            out.append({
                "text": src.get("text", ""),
                "source": src.get("source", ""),
                "score": float(hit.get("_score", 0.0)),
            })
        return out


# ============================================================
# Fusion (RRF)
# ============================================================

def rrf_fusion(
    results_list: list[list[dict]],
    top_k: int = 10,
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion: score(d) = sum_i 1 / (k + rank_i(d))."""
    import hashlib

    scores: dict[str, float] = {}
    bucket: dict[str, dict] = {}

    def _key(item: dict) -> str:
        raw = f"{item.get('source', '')}::{item.get('text', '')}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    for results in results_list:
        for rank, item in enumerate(results):
            key = _key(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in bucket:
                bucket[key] = item

    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    return [{**bucket[k_], "rrf_score": s} for k_, s in ranked]


# ============================================================
# KnowledgeBase (public entry point)
# ============================================================

class KnowledgeBase:
    """Hybrid retrieval: dense k-NN + sparse BM25 against one OpenSearch index."""

    def __init__(
        self,
        endpoint: str | None = None,
        index_name: str | None = None,
        region: str | None = None,
        embedder: BedrockEmbedder | None = None,
        retriever: OpenSearchHybridRetriever | None = None,
    ):
        self.embedder = embedder or BedrockEmbedder()
        self.retriever = retriever or OpenSearchHybridRetriever(
            endpoint=endpoint, index_name=index_name, region=region,
            dim=self.embedder.dim,
        )

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        query_vec = self.embedder.encode_query(query)
        dense_hits = self.retriever.dense_search(query_vec, top_k=20)
        sparse_hits = self.retriever.sparse_search(query, top_k=20)

        if not sparse_hits:
            return dense_hits[:top_k]
        if not dense_hits:
            return sparse_hits[:top_k]

        return rrf_fusion([dense_hits, sparse_hits], top_k=top_k)
