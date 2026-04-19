"""Knowledge retrieval abstraction.

Prefers infra.rag.KnowledgeBase (OpenSearch hybrid: knn_vector + BM25, with
Bedrock Titan embeddings). Falls back to a local txt stub if the real backend
fails to start and the fallback is explicitly allowed by config (dev only by
default).
"""
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)


class _LocalDocKB:
    """Dev-only fallback: loads data/knowledge_docs/*.txt and scores by
    character overlap."""

    def __init__(self, doc_dir: Path):
        self.docs: list[dict] = []
        if not doc_dir.exists():
            return
        for path in sorted(doc_dir.glob("*.txt")):
            text = path.read_text(encoding="utf-8")
            chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
            for i, chunk in enumerate(chunks):
                self.docs.append({
                    "source": f"{path.name}#chunk{i}",
                    "text": chunk,
                })

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if not self.docs:
            return []
        q_chars = set(query)
        scored = []
        for doc in self.docs:
            score = sum(1 for c in q_chars if c in doc["text"])
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: -x[0])
        return [d for _, d in scored[:top_k]]


def _try_load_real_kb():
    try:
        from infra.rag import KnowledgeBase
        return KnowledgeBase(), None
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        if not config.ALLOW_LOCAL_KB_FALLBACK:
            raise RuntimeError(
                f"KnowledgeBase startup failed and local fallback is disabled: {reason}"
            ) from e
        logger.warning(
            "Real KnowledgeBase unavailable (%s); "
            "falling back to local stub at %s",
            reason, config.KNOWLEDGE_DOCS_DIR,
        )
        return _LocalDocKB(config.KNOWLEDGE_DOCS_DIR), reason


class KnowledgeService:
    def __init__(self, kb=None):
        if kb is not None:
            self.kb = kb
            self._fallback_reason: str | None = None
        else:
            self.kb, self._fallback_reason = _try_load_real_kb()

    def is_using_fallback(self) -> bool:
        return isinstance(self.kb, _LocalDocKB)

    def fallback_reason(self) -> str | None:
        return self._fallback_reason

    def search_protocol(self, query: str, top_k: int = 5) -> list[dict]:
        return self.kb.search(query, top_k)
