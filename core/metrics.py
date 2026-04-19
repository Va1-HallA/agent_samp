"""Token + cost accounting via CloudWatch Embedded Metric Format (EMF).

EMF = write a single JSON log line, CloudWatch auto-extracts metrics from it.
Zero API call, zero idle cost; the only charge is the normal log ingestion.

Emitted format (simplified):
    {
      "_aws": {
        "Timestamp": 1700000000000,
        "CloudWatchMetrics": [{
          "Namespace": "CareAgent",
          "Dimensions": [["TenantId", "Model"]],
          "Metrics": [
             {"Name": "InputTokens",  "Unit": "Count"},
             {"Name": "OutputTokens", "Unit": "Count"},
             {"Name": "CostMicroUSD", "Unit": "Count"}
          ]
        }]
      },
      "TenantId": "t1", "Model": "claude-sonnet-4", "InputTokens": 100, ...
    }

CloudWatch agent / Fluent Bit on ECS Fargate picks this up automatically and
publishes it as CloudWatch Metrics under the CareAgent namespace.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("metrics")


# USD per 1M tokens. Bedrock pricing matches direct Anthropic pricing.
_PRICE_TABLE: dict[str, dict[str, float]] = {
    # Anthropic family on Bedrock.
    "anthropic.claude-opus-4-20250514-v1:0": {"input": 15.0, "output": 75.0},
    "anthropic.claude-sonnet-4-20250514-v1:0": {"input": 3.0, "output": 15.0},
    "anthropic.claude-haiku-4-5-20251001-v1:0": {"input": 0.8, "output": 4.0},
    # Embeddings.
    "amazon.titan-embed-text-v2:0": {"input": 0.02, "output": 0.0},
}


def _model_price(model: str) -> dict[str, float]:
    for key, price in _PRICE_TABLE.items():
        if model.startswith(key.split(":")[0].rsplit("-", 1)[0]):
            return price
    return _PRICE_TABLE.get(model, {"input": 0.0, "output": 0.0})


def estimate_cost_usd(model: str, usage: dict[str, int]) -> float:
    p = _model_price(model)
    return (
        usage.get("input_tokens", 0) * p.get("input", 0.0)
        + usage.get("output_tokens", 0) * p.get("output", 0.0)
    ) / 1_000_000


class TokenTracker:
    """Emits EMF log lines; no in-process aggregation state."""

    NAMESPACE = "CareAgent"

    def __init__(self, *_, **__):
        # Args kept for backward compat with server.py construction signature.
        self.enabled = True

    def record(self, tenant_id: str, model: str, usage: Any) -> None:
        u = _coerce_usage(usage)
        cost_u6 = int(round(estimate_cost_usd(model, u) * 1e6))
        payload = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": self.NAMESPACE,
                    "Dimensions": [["TenantId", "Model"]],
                    "Metrics": [
                        {"Name": "InputTokens",  "Unit": "Count"},
                        {"Name": "OutputTokens", "Unit": "Count"},
                        {"Name": "CostMicroUSD", "Unit": "Count"},
                        {"Name": "LLMCalls",     "Unit": "Count"},
                    ],
                }],
            },
            "TenantId": tenant_id,
            "Model": model,
            "InputTokens": u["input_tokens"],
            "OutputTokens": u["output_tokens"],
            "CostMicroUSD": cost_u6,
            "LLMCalls": 1,
        }
        try:
            logger.info(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            logger.warning("EMF emit failed", exc_info=True)

    def summary(self, tenant_id: str) -> dict[str, Any]:
        """Not computed in-process anymore; live numbers are in CloudWatch."""
        return {
            "enabled": True,
            "source": "cloudwatch",
            "hint": f"query via GetMetricData, Namespace={self.NAMESPACE}, TenantId={tenant_id}",
        }


def _coerce_usage(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0}
    if isinstance(usage, dict):
        src = usage
    else:
        src = {
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        }
    return {
        "input_tokens": int(src.get("input_tokens") or 0),
        "output_tokens": int(src.get("output_tokens") or 0),
    }
