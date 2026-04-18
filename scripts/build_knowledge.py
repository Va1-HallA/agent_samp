"""Offline knowledge index builder.

    python -m scripts.build_knowledge

Reads data/knowledge_docs/*.txt, chunks them, embeds with BGE, and writes
to Milvus (dense) + Elasticsearch (sparse / BM25).
"""
import glob

import config
from infra.rag import Chunker, Embedder, MilvusDenseRetriever, ESRetriever


def load_files(path_pattern: str) -> list[dict]:
    """Read *.txt files; returns [{source, content}, ...]."""
    docs = []
    for file in glob.glob(path_pattern):
        with open(file, "r", encoding="utf-8") as f:
            docs.append({
                "source": file.split("/")[-1],
                "content": f.read(),
            })
    return docs


def build():
    doc_dir = config.KNOWLEDGE_DOCS_DIR
    pattern = str(doc_dir / "*.txt")
    docs = load_files(pattern)
    if not docs:
        raise FileNotFoundError(f"no txt found under {doc_dir}")
    print(f"loaded {len(docs)} docs")

    chunker = Chunker()
    corpus: list[dict] = []
    for doc in docs:
        corpus.extend(chunker.split(doc["source"], doc["content"]))
    print(f"chunks: {len(corpus)}")

    texts = [c["text"] for c in corpus]
    sources = [f"{c['source']}@{c['char_start']}" for c in corpus]

    print("loading embedding model ...")
    embedder = Embedder()

    print("building dense index (Milvus) ...")
    dense = MilvusDenseRetriever(dim=embedder.dim)
    dense.reset()
    embeddings = embedder.encode_docs(texts)
    dense.insert(texts, sources, embeddings)
    print(f"  milvus collection '{dense.collection_name}': {len(corpus)} chunks")

    print("building sparse index (Elasticsearch BM25) ...")
    sparse = ESRetriever()
    sparse.reset()
    sparse.bulk_insert([{"text": t, "source": s} for t, s in zip(texts, sources)])
    print(f"  es index '{sparse.index_name}': {len(corpus)} chunks")

    print("done.")


if __name__ == "__main__":
    build()
