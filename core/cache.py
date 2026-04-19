"""Exact-match query cache.

Left as a no-op in AWS mode (ElastiCache was dropped to avoid idle cost).
The API surface is preserved so callers do not have to branch. A real
implementation could be added later — DynamoDB with a TTL attribute is the
natural choice: single-digit ms latency, zero idle cost, TTL auto-expires rows.
"""
import logging

logger = logging.getLogger(__name__)


class QueryCache:
    def __init__(self, *_, **__):
        self.enabled = False

    def get(self, tenant_id: str, query: str) -> str | None:
        return None

    def set(self, tenant_id: str, query: str, response: str) -> None:
        return None

    def invalidate_tenant(self, tenant_id: str) -> int:
        return 0
