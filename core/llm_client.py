"""Anthropic call wrapper with retry, timeout, tracing and usage tracking."""
import logging
import time

import anthropic
from anthropic.types import Message
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception_type,
    before_sleep_log,
)

import config
from core.context import get_tenant_id
from core.metrics import TokenTracker, estimate_cost_usd, _coerce_usage
from core.tracing import emit_event

logger = logging.getLogger(__name__)


_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
)


# Module-level tracker; replaced with a Redis-backed instance by api/server.py.
_token_tracker: TokenTracker = TokenTracker(redis_client=None)


def set_token_tracker(tracker: TokenTracker) -> None:
    global _token_tracker
    _token_tracker = tracker


@retry(
    stop=stop_after_attempt(config.LLM_MAX_RETRIES),
    wait=wait_exponential_jitter(initial=1, max=10),
    retry=retry_if_exception_type(_RETRYABLE),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def call_messages(client: anthropic.Anthropic, **kwargs) -> Message:
    """messages.create with retry, timeout, trace event and usage tracking."""
    kwargs.setdefault("timeout", config.LLM_TIMEOUT)
    model = kwargs.get("model", "unknown")

    t0 = time.time() * 1000
    try:
        resp = client.messages.create(**kwargs)
    except Exception as e:
        emit_event(
            "llm.call_failed",
            model=model,
            latency_ms=round(time.time() * 1000 - t0, 1),
            error_type=type(e).__name__,
        )
        raise

    elapsed = round(time.time() * 1000 - t0, 1)
    usage_dict = _coerce_usage(getattr(resp, "usage", None))
    cost = estimate_cost_usd(model, usage_dict)

    emit_event(
        "llm.call",
        model=model,
        latency_ms=elapsed,
        input_tokens=usage_dict["input_tokens"],
        output_tokens=usage_dict["output_tokens"],
        cache_read=usage_dict["cache_read_input_tokens"],
        cache_write=usage_dict["cache_creation_input_tokens"],
        cost_usd=round(cost, 6),
        stop_reason=getattr(resp, "stop_reason", None),
    )

    try:
        _token_tracker.record(get_tenant_id(), model, usage_dict)
    except Exception:
        logger.warning("token tracker record failed", exc_info=True)

    return resp
