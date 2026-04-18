"""Lightweight structured tracing.

ContextVar carries request_id + tenant_id across the whole request so log
lines can be correlated. Events are emitted as JSON logs.

Usage:
    start_request(tenant_id="t1")      # in middleware
    emit_event("route.decided", route="both")
    end_request(status="ok")
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger("trace")

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_request_start_ms: ContextVar[float] = ContextVar("request_start_ms", default=0.0)
_request_tenant: ContextVar[str] = ContextVar("request_tenant", default="-")


def start_request(tenant_id: str, request_id: str | None = None) -> str:
    rid = request_id or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    _request_tenant.set(tenant_id)
    _request_start_ms.set(time.time() * 1000)
    emit_event("request.start", tenant_id=tenant_id)
    return rid


def end_request(status: str = "ok", **extra: Any) -> None:
    start_ms = _request_start_ms.get()
    elapsed = round(time.time() * 1000 - start_ms, 1) if start_ms else 0.0
    emit_event("request.end", status=status, latency_ms=elapsed, **extra)


def get_request_id() -> str:
    return _request_id.get()


def emit_event(event: str, **fields: Any) -> None:
    """Write a single JSON log line."""
    payload = {
        "event": event,
        "request_id": _request_id.get(),
        "tenant_id": _request_tenant.get(),
        **fields,
    }
    try:
        msg = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        msg = str(payload)
    logger.info(msg)


class Timer:
    """Context manager that emits <name>.done / <name>.error with latency."""

    def __init__(self, name: str, **fields: Any):
        self.name = name
        self.fields = fields
        self._t0 = 0.0

    def __enter__(self) -> "Timer":
        self._t0 = time.time() * 1000
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = round(time.time() * 1000 - self._t0, 1)
        if exc_type is None:
            emit_event(f"{self.name}.done", latency_ms=elapsed, **self.fields)
        else:
            emit_event(
                f"{self.name}.error",
                latency_ms=elapsed,
                error_type=exc_type.__name__,
                error_msg=str(exc)[:200],
                **self.fields,
            )
