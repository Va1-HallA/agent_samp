"""Offline knowledge index builder.

    python -m scripts.build_knowledge

Reads data/knowledge_docs/*.txt, chunks them, embeds with Bedrock Titan v2,
and writes to a single OpenSearch index holding both the text and the
knn_vector for hybrid retrieval at query time.

The script is idempotent: ``reset=True`` drops and recreates the index so a
re-run doesn't duplicate chunks.
"""
import glob

import config
from infra.rag import BedrockEmbedder, Chunker, OpenSearchHybridRetriever


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


def build(reset: bool = True):
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

    print(f"embedding via Bedrock Titan ({config.BEDROCK_EMBEDDING_MODEL_ID}) ...")
    embedder = BedrockEmbedder()
    embeddings = embedder.encode_docs(texts)

    print(f"writing to OpenSearch index '{config.OPENSEARCH_INDEX}' ...")
    retriever = OpenSearchHybridRetriever(dim=embedder.dim)
    if reset:
        retriever.reset()
    else:
        retriever.ensure_index()

    retriever.bulk_insert([
        {"text": t, "source": s, "embedding": e}
        for t, s, e in zip(texts, sources, embeddings)
    ])
    print(f"  indexed {len(corpus)} chunks")
    print("done.")


if __name__ == "__main__":
    build()
