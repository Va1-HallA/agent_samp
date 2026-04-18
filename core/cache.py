"""Exact-match query cache backed by Redis.

Key format: qcache:{tenant_id}:{md5(query)}
All operations are no-ops when redis_client is None.
"""
import hashlib
import logging

import config

logger = logging.getLogger(__name__)


def _make_key(tenant_id: str, query: str) -> str:
    norm = query.strip()
    digest = hashlib.md5(norm.encode("utf-8")).hexdigest()
    return f"qcache:{tenant_id}:{digest}"


class QueryCache:
    def __init__(self, redis_client=None, ttl: int = config.CACHE_TTL_SECONDS):
        self.redis = redis_client
        self.ttl = ttl
        self.enabled = config.CACHE_ENABLED and redis_client is not None

    def get(self, tenant_id: str, query: str) -> str | None:
        if not self.enabled:
            return None
        try:
            return self.redis.get(_make_key(tenant_id, query))
        except Exception:
            logger.warning("cache get failed", exc_info=True)
            return None

    def set(self, tenant_id: str, query: str, response: str) -> None:
        if not self.enabled:
            return
        try:
            self.redis.set(_make_key(tenant_id, query), response, ex=self.ttl)
        except Exception:
            logger.warning("cache set failed", exc_info=True)

    def invalidate_tenant(self, tenant_id: str) -> int:
        """Drop all cache entries for a tenant. Uses SCAN to avoid blocking."""
        if not self.enabled:
            return 0
        pattern = f"qcache:{tenant_id}:*"
        count = 0
        try:
            for key in self.redis.scan_iter(match=pattern, count=500):
                self.redis.delete(key)
                count += 1
        except Exception:
            logger.warning("cache invalidate failed", exc_info=True)
        return count
