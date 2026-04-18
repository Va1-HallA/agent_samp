"""Token and cost accounting per tenant + model, backed by a Redis hash."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Unit: USD per 1M tokens.
_PRICE_TABLE: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku-4-5-20251001": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
}


def _model_price(model: str) -> dict[str, float]:
    return _PRICE_TABLE.get(model, {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0})


def estimate_cost_usd(model: str, usage: dict[str, int]) -> float:
    p = _model_price(model)
    return (
        usage.get("input_tokens", 0) * p["input"]
        + usage.get("output_tokens", 0) * p["output"]
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
    ) / 1_000_000


class TokenTracker:
    """Per-tenant token + cost tracker. No-op when redis_client is None."""

    def __init__(self, redis_client: Any = None):
        self.redis = redis_client

    @staticmethod
    def _key(tenant_id: str) -> str:
        return f"metrics:{tenant_id}"

    def record(self, tenant_id: str, model: str, usage: Any) -> None:
        """usage may be an anthropic.types.Usage or a dict."""
        if self.redis is None:
            return
        u = _coerce_usage(usage)
        try:
            key = self._key(tenant_id)
            pipe = self.redis.pipeline()
            pipe.hincrby(key, f"{model}:calls", 1)
            pipe.hincrby(key, f"{model}:in", u["input_tokens"])
            pipe.hincrby(key, f"{model}:out", u["output_tokens"])
            pipe.hincrby(key, f"{model}:cache_read", u["cache_read_input_tokens"])
            pipe.hincrby(key, f"{model}:cache_write", u["cache_creation_input_tokens"])
            # Store cost as integer micro-dollars to avoid float errors in HINCRBY.
            cost_cents = int(round(estimate_cost_usd(model, u) * 1e6))
            pipe.hincrby(key, f"{model}:cost_u6", cost_cents)
            pipe.execute()
        except Exception:
            logger.warning("token metrics record failed", exc_info=True)

    def summary(self, tenant_id: str) -> dict[str, Any]:
        if self.redis is None:
            return {"enabled": False}
        try:
            raw = self.redis.hgetall(self._key(tenant_id)) or {}
        except Exception:
            logger.warning("token metrics fetch failed", exc_info=True)
            return {"enabled": True, "error": "redis_unavailable"}

        per_model: dict[str, dict[str, Any]] = {}
        total_cost_usd = 0.0
        for k, v in raw.items():
            model, field = k.rsplit(":", 1)
            per_model.setdefault(model, {"calls": 0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0})
            if field == "cost_u6":
                cost = int(v) / 1e6
                per_model[model]["cost_usd"] = round(cost, 6)
                total_cost_usd += cost
            elif field in {"calls", "in", "out", "cache_read", "cache_write"}:
                per_model[model][field] = int(v)
        return {
            "enabled": True,
            "tenant_id": tenant_id,
            "per_model": per_model,
            "total_cost_usd": round(total_cost_usd, 6),
        }


def _coerce_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    if isinstance(usage, dict):
        src = usage
    else:
        src = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
    return {
        "input_tokens": int(src.get("input_tokens") or 0),
        "output_tokens": int(src.get("output_tokens") or 0),
        "cache_read_input_tokens": int(src.get("cache_read_input_tokens") or 0),
        "cache_creation_input_tokens": int(src.get("cache_creation_input_tokens") or 0),
    }
