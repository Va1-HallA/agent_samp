"""Orchestrator-Worker coordinator (async).

Pipeline:
    1. Input guardrail -> cache lookup (return on hit).
    2. LLM route decision via lightweight ROUTER_MODEL.
    3. Dispatch to specialist agents: triage / protocol / both (serial) / direct.
    4. Merge triage + protocol into a final report.
    5. Auto-create alert when triage severity is high.
    6. Write to cache.

Blocking LLM calls are dispatched via asyncio.to_thread so the event loop
stays free to serve other requests.
"""
import asyncio
import json
import logging
import re
from typing import AsyncIterator

import anthropic

import config
from agents.triage_agent import create_triage_agent
from agents.protocol_agent import create_protocol_agent
from core.prompt_template import ROUTER_PROMPT, MERGE_PROMPT
from core.guardrails import (
    GuardrailViolation,
    SAFE_FALLBACK,
    check_input,
    check_output,
)
from core.llm_client import call_messages
from core.cache import QueryCache
from core.context import get_tenant_id
from core.tracing import emit_event, Timer
from services.health_service import HealthService
from services.alert_service import AlertService
from services.knowledge_service import KnowledgeService

logger = logging.getLogger(__name__)

_VALID_ROUTES = {"triage", "protocol", "both", "direct"}

_DIRECT_SYSTEM = "You are a care assistant. Answer general questions concisely."


async def _iterate_stream(stream_ctx) -> AsyncIterator[str]:
    """Adapt the anthropic synchronous stream context to an async iterator."""
    def _enter():
        return stream_ctx.__enter__()

    def _exit(exc_type=None, exc=None, tb=None):
        try:
            stream_ctx.__exit__(exc_type, exc, tb)
        except Exception:
            pass

    inner = await asyncio.to_thread(_enter)
    try:
        iterator = iter(inner.text_stream)
        while True:
            chunk = await asyncio.to_thread(next, iterator, _SENTINEL)
            if chunk is _SENTINEL:
                break
            if chunk:
                yield chunk
    finally:
        await asyncio.to_thread(_exit)


_SENTINEL = object()


class Coordinator:
    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str,
        router_model: str | None = None,
        health: HealthService | None = None,
        alert: AlertService | None = None,
        knowledge: KnowledgeService | None = None,
        cache: QueryCache | None = None,
    ):
        self.client = client
        self.model = model
        self.router_model = router_model or config.ROUTER_MODEL
        self.health = health or HealthService()
        self.alert = alert or AlertService()
        self.knowledge = knowledge or KnowledgeService()
        self.cache = cache or QueryCache(redis_client=None)

        self.triage = create_triage_agent(client, model, health=self.health)
        self.protocol = create_protocol_agent(client, model, knowledge=self.knowledge)

    async def run(self, query: str, chat_history: list[dict] | None = None) -> str:
        try:
            check_input(query)
        except GuardrailViolation as e:
            return f"{SAFE_FALLBACK} (blocked: {e.reason})"

        tenant_id = get_tenant_id()

        # Only cache single-turn queries; context-dependent answers must not
        # leak across sessions.
        cacheable = not chat_history
        if cacheable:
            hit = self.cache.get(tenant_id, query)
            if hit:
                emit_event("cache.hit", layer="query", tenant_id=tenant_id)
                return hit

        try:
            with Timer("route.decide"):
                route = await asyncio.to_thread(self._decide_route, query)
            emit_event("route.decided", route=route)

            if route == "direct":
                with Timer("agent.direct"):
                    answer = await asyncio.to_thread(self._direct_answer, query, chat_history)
                answer = self._safe(answer)
                if cacheable and answer != SAFE_FALLBACK:
                    self.cache.set(tenant_id, query, answer)
                return answer

            triage_result = None
            protocol_result = None

            if route in ("triage", "both"):
                with Timer("agent.triage"):
                    triage_result = await asyncio.to_thread(self.triage.run, query)

            if route in ("protocol", "both"):
                pq = query
                if triage_result:
                    pq = (
                        f"[Triage conclusion]\n{triage_result}\n\n"
                        f"Based on the conclusion above, search for relevant care protocols and give concrete recommendations."
                    )
                with Timer("agent.protocol"):
                    protocol_result = await asyncio.to_thread(self.protocol.run, pq)

            with Timer("agent.merge"):
                final = await asyncio.to_thread(self._merge, query, triage_result, protocol_result)

            if triage_result and self._is_high_severity(triage_result):
                emit_event("alert.auto_trigger")
                await asyncio.to_thread(self._auto_alert, query, triage_result)

            answer = self._safe(final)
            if cacheable and answer != SAFE_FALLBACK:
                self.cache.set(tenant_id, query, answer)
            return answer
        except anthropic.APIError as e:
            logger.exception("LLM API error in Coordinator.run")
            return f"{SAFE_FALLBACK} (LLM service error: {type(e).__name__})"
        except Exception as e:
            logger.exception("Unexpected error in Coordinator.run")
            return f"{SAFE_FALLBACK} (internal error: {type(e).__name__})"

    async def run_stream(
        self, query: str, chat_history: list[dict] | None = None
    ) -> AsyncIterator[dict]:
        """Streaming variant. Yields SSE events:
            {"event": "phase",  "data": {...}}
            {"event": "token",  "data": "..."}
            {"event": "done",   "data": {"full": "..."}}
            {"event": "error",  "data": {"reason": "..."}}

        Tool-using child agents cannot be streamed (tool_use blocks are atomic);
        only the final merge / direct answer is streamed token by token.
        Streaming path does not use the query cache.
        """
        try:
            check_input(query)
        except GuardrailViolation as e:
            yield {"event": "error", "data": {"reason": f"input_blocked:{e.reason}"}}
            return

        try:
            route = await asyncio.to_thread(self._decide_route, query)
            emit_event("route.decided", route=route, streaming=True)
            yield {"event": "phase", "data": {"stage": "route", "value": route}}

            if route == "direct":
                full = ""
                async for chunk in self._stream_direct(query, chat_history):
                    full += chunk
                    yield {"event": "token", "data": chunk}
                full = self._safe(full)
                yield {"event": "done", "data": {"full": full}}
                return

            triage_result = None
            protocol_result = None

            if route in ("triage", "both"):
                with Timer("agent.triage"):
                    triage_result = await asyncio.to_thread(self.triage.run, query)
                yield {"event": "phase", "data": {"stage": "triage_done", "text": triage_result}}

            if route in ("protocol", "both"):
                pq = query
                if triage_result:
                    pq = (
                        f"[Triage conclusion]\n{triage_result}\n\n"
                        f"Based on the conclusion above, search for relevant care protocols and give concrete recommendations."
                    )
                with Timer("agent.protocol"):
                    protocol_result = await asyncio.to_thread(self.protocol.run, pq)
                yield {"event": "phase", "data": {"stage": "protocol_done", "text": protocol_result}}

            full = ""
            async for chunk in self._stream_merge(query, triage_result, protocol_result):
                full += chunk
                yield {"event": "token", "data": chunk}

            if triage_result and self._is_high_severity(triage_result):
                emit_event("alert.auto_trigger", streaming=True)
                await asyncio.to_thread(self._auto_alert, query, triage_result)
                yield {"event": "phase", "data": {"stage": "alert_created"}}

            full = self._safe(full)
            yield {"event": "done", "data": {"full": full}}
        except anthropic.APIError as e:
            logger.exception("LLM API error in Coordinator.run_stream")
            yield {"event": "error", "data": {"reason": f"llm_api:{type(e).__name__}"}}
        except Exception:
            logger.exception("Unexpected error in Coordinator.run_stream")
            yield {"event": "error", "data": {"reason": "internal_error"}}

    async def _stream_direct(
        self, query: str, history: list[dict] | None
    ) -> AsyncIterator[str]:
        messages = list(history) if history else []
        messages.append({"role": "user", "content": query})

        def _run():
            return self.client.messages.stream(
                model=self.model,
                max_tokens=512,
                system=_DIRECT_SYSTEM,
                messages=messages,
            )

        stream_ctx = await asyncio.to_thread(_run)
        async for chunk in _iterate_stream(stream_ctx):
            yield chunk

    async def _stream_merge(
        self, query: str, triage: str | None, protocol: str | None
    ) -> AsyncIterator[str]:
        parts = [f"[User question]\n{query}\n"]
        if triage:
            parts.append(f"[Triage assessment]\n{triage}\n")
        if protocol:
            parts.append(f"[Care protocol]\n{protocol}\n")

        def _run():
            return self.client.messages.stream(
                model=self.model,
                max_tokens=1024,
                system=MERGE_PROMPT,
                messages=[{"role": "user", "content": "\n".join(parts)}],
            )

        stream_ctx = await asyncio.to_thread(_run)
        async for chunk in _iterate_stream(stream_ctx):
            yield chunk

    # ---------- Internal ----------

    def _decide_route(self, query: str) -> str:
        try:
            resp = call_messages(
                self.client,
                model=self.router_model,
                max_tokens=100,
                system=ROUTER_PROMPT,
                messages=[{"role": "user", "content": query}],
            )
        except anthropic.APIError:
            logger.warning("route LLM call failed, default to 'both'", exc_info=True)
            return "both"

        text = ""
        for block in resp.content:
            if block.type == "text":
                text = block.text
                break

        route: str | None = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                route = parsed.get("route")
        except (json.JSONDecodeError, ValueError):
            pass

        if route not in _VALID_ROUTES:
            # Fallback to keyword match when the JSON is invalid.
            lower = text.lower()
            for r in ("both", "triage", "protocol", "direct"):
                if r in lower:
                    return r
            return "both"
        return route

    def _direct_answer(self, query: str, history: list[dict] | None) -> str:
        messages = list(history) if history else []
        messages.append({"role": "user", "content": query})
        try:
            resp = call_messages(
                self.client,
                model=self.model,
                max_tokens=512,
                system=_DIRECT_SYSTEM,
                messages=messages,
            )
        except anthropic.APIError:
            logger.warning("direct_answer LLM call failed", exc_info=True)
            return SAFE_FALLBACK

        for block in resp.content:
            if block.type == "text":
                return block.text
        return ""

    def _merge(self, query: str, triage: str | None, protocol: str | None) -> str:
        if not triage and not protocol:
            return "no result"
        parts = [f"[User question]\n{query}\n"]
        if triage:
            parts.append(f"[Triage assessment]\n{triage}\n")
        if protocol:
            parts.append(f"[Care protocol]\n{protocol}\n")

        try:
            resp = call_messages(
                self.client,
                model=self.model,
                max_tokens=1024,
                system=MERGE_PROMPT,
                messages=[{"role": "user", "content": "\n".join(parts)}],
            )
            for block in resp.content:
                if block.type == "text":
                    return block.text
        except anthropic.APIError:
            logger.warning("merge LLM call failed, returning raw sections", exc_info=True)

        # Fallback: concatenate raw sections so the user still gets information.
        return "\n\n".join(parts)

    @staticmethod
    def _safe(text: str) -> str:
        try:
            return check_output(text)
        except GuardrailViolation:
            return SAFE_FALLBACK

    @staticmethod
    def _is_high_severity(triage_result: str) -> bool:
        return bool(re.search(r"severity[:：]?\s*high", triage_result, re.IGNORECASE))

    def _auto_alert(self, query: str, triage_result: str) -> None:
        """Best-effort alert creation on high severity."""
        try:
            spec_name = self._match_resident(query)
            if not spec_name:
                logger.warning("auto_alert skipped: no resident name matched in query")
                return
            profile = self.health.get_resident_profile(spec_name)
            if not profile:
                logger.warning("auto_alert skipped: profile not found for %s", spec_name)
                return
            self.alert.create_alert(
                resident_id=profile["id"],
                alert_type="vital_signs_abnormal",
                description=f"{query}\nTriage summary: {triage_result[:200]}",
                severity="high",
            )
        except Exception:
            logger.exception("auto_alert failed")

    def _match_resident(self, query: str) -> str | None:
        for name in self._resident_names():
            if name in query:
                return name
        return None

    def _resident_names(self) -> list[str]:
        try:
            return self.health.list_resident_names()
        except Exception:
            logger.exception("list_resident_names failed")
            return []
