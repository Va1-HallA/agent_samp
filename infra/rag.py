"""RAG retrieval stack.

    Chunker -> Embedder -> MilvusDenseRetriever ┐
                           ESRetriever (BM25)    ├-> rrf_fusion -> KnowledgeBase
                                                 ┘

KnowledgeBase is the only public entry point; service layers call .search().
"""
from __future__ import annotations

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk
from pymilvus import (
    connections, Collection, FieldSchema, CollectionSchema, DataType, utility,
)
from sentence_transformers import SentenceTransformer

import config


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

            # For non-tail chunks, snap back to the nearest separator.
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
# Embedder (Dense)
# ============================================================

class Embedder:
    """BGE embedding. Query side auto-prepends the BGE instruction."""

    _QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

    def __init__(self, model_name: str | None = None):
        model_name = model_name or config.EMBEDDING_MODEL
        self.model = SentenceTransformer(model_name)
        self.dim: int = self.model.get_sentence_embedding_dimension()

    def encode_docs(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        vecs = self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vecs.tolist()

    def encode_query(self, query: str) -> list[float]:
        prefixed = self._QUERY_INSTRUCTION + query
        vec = self.model.encode([prefixed], normalize_embeddings=True, show_progress_bar=False)
        return vec[0].tolist()


# ============================================================
# Milvus (Dense retriever)
# ============================================================

class MilvusDenseRetriever:
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        collection_name: str | None = None,
        dim: int = 512,
    ):
        self.host = host or config.MILVUS_HOST
        self.port = port or config.MILVUS_PORT
        self.collection_name = collection_name or config.MILVUS_COLLECTION
        self.dim = dim
        self._connect()
        self.collection = self._ensure_collection()
        self._loaded = False
        self._try_load()

    def _try_load(self) -> None:
        try:
            self.collection.load()
            self._loaded = True
        except Exception:
            # May fail on empty collection; retried during search.
            self._loaded = False

    def _connect(self):
        if not connections.has_connection("default"):
            connections.connect("default", host=self.host, port=str(self.port))

    def _ensure_collection(self) -> Collection:
        if utility.has_collection(self.collection_name):
            return Collection(self.collection_name)

        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=2000),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=300),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dim),
        ]
        schema = CollectionSchema(fields, description="CareAgent knowledge base")
        col = Collection(self.collection_name, schema)
        col.create_index(
            field_name="embedding",
            index_params={
                "index_type": "IVF_FLAT",
                "metric_type": "COSINE",
                "params": {"nlist": 64},
            },
        )
        return col

    def reset(self) -> None:
        """Drop + recreate. Used by the offline build script."""
        if utility.has_collection(self.collection_name):
            utility.drop_collection(self.collection_name)
        self.collection = self._ensure_collection()
        self._loaded = False

    def insert(
        self,
        texts: list[str],
        sources: list[str],
        embeddings: list[list[float]],
    ) -> None:
        assert len(texts) == len(sources) == len(embeddings)
        self.collection.insert([texts, sources, embeddings])
        self.collection.flush()
        self._loaded = False

    def search(self, query_vec: list[float], top_k: int = 10) -> list[dict]:
        if not self._loaded:
            self._try_load()
        results = self.collection.search(
            data=[query_vec],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 8}},
            limit=top_k,
            output_fields=["text", "source"],
        )
        return [
            {
                "text": hit.entity.get("text"),
                "source": hit.entity.get("source"),
                "score": float(hit.distance),
            }
            for hit in results[0]
        ]


# ============================================================
# Elasticsearch (Sparse retriever, BM25)
# ============================================================

class ESRetriever:
    """BM25 retriever over Elasticsearch with a CJK analyzer."""

    def __init__(
        self,
        url: str | None = None,
        index_name: str | None = None,
    ):
        self.url = url or config.ES_URL
        self.index_name = index_name or config.ES_INDEX
        self.client = Elasticsearch(self.url, request_timeout=30)

    def reset(self) -> None:
        if self.client.indices.exists(index=self.index_name):
            self.client.indices.delete(index=self.index_name)
        self._create_index()

    def _create_index(self) -> None:
        self.client.indices.create(
            index=self.index_name,
            body={
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "analysis": {
                        "analyzer": {
                            "cn_analyzer": {"type": "cjk"}
                        }
                    },
                },
                "mappings": {
                    "properties": {
                        "text": {"type": "text", "analyzer": "cn_analyzer"},
                        "source": {"type": "keyword"},
                    }
                },
            },
        )

    def ensure_index(self) -> None:
        if not self.client.indices.exists(index=self.index_name):
            self._create_index()

    def bulk_insert(self, corpus: list[dict]) -> None:
        """corpus: [{text, source}, ...]"""
        self.ensure_index()
        actions = [
            {"_index": self.index_name, "_source": {"text": d["text"], "source": d["source"]}}
            for d in corpus
        ]
        bulk(self.client, actions, refresh="wait_for")

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        if not self.client.indices.exists(index=self.index_name):
            return []
        resp = self.client.search(
            index=self.index_name,
            body={
                "query": {
                    "match": {
                        "text": {"query": query, "operator": "or"}
                    }
                },
                "size": top_k,
            },
        )
        hits = []
        for hit in resp["hits"]["hits"]:
            src = hit["_source"]
            hits.append({
                "text": src["text"],
                "source": src["source"],
                "score": float(hit["_score"]),
            })
        return hits


# ============================================================
# Fusion
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
    """Combines dense (Milvus) + sparse (ES) retrieval with RRF fusion."""

    def __init__(
        self,
        milvus_host: str | None = None,
        milvus_port: int | None = None,
        collection_name: str | None = None,
        embedding_model: str | None = None,
        es_url: str | None = None,
        es_index: str | None = None,
    ):
        self.embedder = Embedder(embedding_model)
        self.dense = MilvusDenseRetriever(
            host=milvus_host,
            port=milvus_port,
            collection_name=collection_name,
            dim=self.embedder.dim,
        )
        self.sparse = ESRetriever(url=es_url, index_name=es_index)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        query_vec = self.embedder.encode_query(query)
        dense_hits = self.dense.search(query_vec, top_k=20)
        sparse_hits = self.sparse.search(query, top_k=20)

        if not sparse_hits:
            return dense_hits[:top_k]
        if not dense_hits:
            return sparse_hits[:top_k]

        return rrf_fusion([dense_hits, sparse_hits], top_k=top_k)
