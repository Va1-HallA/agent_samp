"""LLMBackend call wrapper: retry, timeout, tracing, usage tracking.

Backend-agnostic: the decorator retries on ``RetryableLLMError`` regardless of
who raised it (Bedrock, a future Anthropic-direct backend, a local stub for
tests).
"""
import logging
import time

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception_type,
    before_sleep_log,
)

import config
from core.context import get_tenant_id
from core.llm_backend import LLMBackend, LLMResponse, RetryableLLMError
from core.metrics import TokenTracker, estimate_cost_usd, _coerce_usage
from core.tracing import emit_event

logger = logging.getLogger(__name__)


# Module-level tracker; server.py may replace it with a custom instance.
_token_tracker: TokenTracker = TokenTracker()


def set_token_tracker(tracker: TokenTracker) -> None:
    global _token_tracker
    _token_tracker = tracker


@retry(
    stop=stop_after_attempt(config.LLM_MAX_RETRIES),
    wait=wait_exponential_jitter(initial=1, max=10),
    retry=retry_if_exception_type(RetryableLLMError),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def call_messages(
    llm: LLMBackend,
    *,
    model: str,
    system: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_tokens: int = 2048,
    timeout: float | None = None,
) -> LLMResponse:
    """Invoke LLMBackend.create with retry, tracing and usage tracking."""
    t0 = time.time() * 1000
    try:
        resp = llm.create(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            timeout=timeout if timeout is not None else config.LLM_TIMEOUT,
        )
    except Exception as e:
        emit_event(
            "llm.call_failed",
            model=model,
            latency_ms=round(time.time() * 1000 - t0, 1),
            error_type=type(e).__name__,
        )
        raise

    elapsed = round(time.time() * 1000 - t0, 1)
    usage_dict = _coerce_usage(resp.usage)
    cost = estimate_cost_usd(model, usage_dict)

    emit_event(
        "llm.call",
        model=model,
        latency_ms=elapsed,
        input_tokens=usage_dict["input_tokens"],
        output_tokens=usage_dict["output_tokens"],
        cost_usd=round(cost, 6),
        stop_reason=resp.stop_reason,
    )

    try:
        _token_tracker.record(get_tenant_id(), model, usage_dict)
    except Exception:
        logger.warning("token tracker record failed", exc_info=True)

    return resp
