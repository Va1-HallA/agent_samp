"""FastAPI HTTP server.

Run locally:
    uvicorn api.server:app --reload --host 0.0.0.0 --port 8000

On AWS (ECS Fargate) the Dockerfile's CMD boots uvicorn the same way.
"""
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import config
from agents.coordinator import Coordinator
from core.cache import QueryCache
from core.context import set_tenant_id, get_tenant_id
from core.guardrails import SAFE_FALLBACK
from core.llm_backend import BedrockBackend, LLMBackend
from core.llm_client import set_token_tracker
from core.memory import ChatMemory
from core.metrics import TokenTracker
from core.tracing import start_request, end_request
from infra.session_store import SessionStore, build_session_store

logger = logging.getLogger(__name__)


# ---------- Process-wide singletons ----------

class _State:
    llm: LLMBackend | None = None
    coordinator: Coordinator | None = None
    cache: QueryCache | None = None
    tokens: TokenTracker | None = None
    sessions: SessionStore | None = None


state = _State()


class TenantResolutionError(Exception):
    pass


def _resolve_tenant_id(request: Request, *, strict: bool) -> str:
    if config.TENANT_SOURCE == "header":
        tenant = request.headers.get(config.TENANT_HEADER, "").strip()
        if tenant:
            return tenant
        if strict:
            raise TenantResolutionError(f"missing tenant header: {config.TENANT_HEADER}")
        return config.DEFAULT_TENANT_ID

    if config.TENANT_SOURCE == "trusted_header":
        tenant = request.headers.get(config.TRUSTED_TENANT_HEADER, "").strip()
        if tenant:
            return tenant
        raise TenantResolutionError(
            f"missing trusted tenant header: {config.TRUSTED_TENANT_HEADER}"
        )

    raise TenantResolutionError(f"unsupported TENANT_SOURCE: {config.TENANT_SOURCE}")


def _rate_limit_key(request: Request) -> str:
    """Bucket rate limits per tenant."""
    try:
        return _resolve_tenant_id(request, strict=False)
    except TenantResolutionError:
        return "unauthenticated"


limiter = Limiter(key_func=_rate_limit_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.llm = BedrockBackend(region=config.AWS_REGION, timeout=config.LLM_TIMEOUT)
    state.sessions = build_session_store()
    state.cache = QueryCache()
    state.tokens = TokenTracker()
    set_token_tracker(state.tokens)
    state.coordinator = Coordinator(
        llm=state.llm,
        model=config.BEDROCK_MODEL_ID,
        cache=state.cache,
    )
    yield


app = FastAPI(title="CareAgent API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------- Middleware ----------

@app.middleware("http")
async def tenant_middleware(request: Request, call_next):
    try:
        tenant = _resolve_tenant_id(request, strict=True)
    except TenantResolutionError as e:
        return JSONResponse(status_code=401, content={"detail": str(e)})

    set_tenant_id(tenant)
    start_request(tenant_id=tenant)
    try:
        response = await call_next(request)
        end_request(status="ok", path=request.url.path, method=request.method,
                    http_status=response.status_code)
        return response
    except Exception as e:
        end_request(status="error", path=request.url.path, method=request.method,
                    error_type=type(e).__name__)
        raise


# ---------- Pydantic ----------

class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
    tenant_id: str


# ---------- Memory helpers ----------

def _load_memory(tenant_id: str, session_id: str) -> ChatMemory:
    memory = ChatMemory(llm=state.llm, model=config.BEDROCK_MODEL_ID)
    stored = state.sessions.load(tenant_id, session_id) if state.sessions else None
    if stored:
        memory.summary = stored.get("summary", "") or ""
        memory.messages = stored.get("messages", []) or []
    return memory


def _save_memory(tenant_id: str, session_id: str, memory: ChatMemory) -> None:
    if state.sessions is None:
        return
    state.sessions.save(tenant_id, session_id, {
        "summary": memory.summary,
        "messages": memory.messages,
    })


# ---------- Endpoints ----------

@app.get("/health")
def health():
    ks = state.coordinator.knowledge if state.coordinator else None
    return {
        "status": "ok",
        "model": config.BEDROCK_MODEL_ID,
        "router_model": config.BEDROCK_ROUTER_MODEL_ID,
        "embedding_model": config.BEDROCK_EMBEDDING_MODEL_ID,
        "app_env": config.APP_ENV,
        "tenant_source": config.TENANT_SOURCE,
        "session_store": type(state.sessions).__name__ if state.sessions else None,
        "inproc_memory_fallback": config.ALLOW_INPROC_MEMORY_FALLBACK,
        "cache_enabled": state.cache.enabled if state.cache else False,
        "knowledge_fallback": ks.is_using_fallback() if ks else None,
        "knowledge_reason": ks.fallback_reason() if ks else None,
    }


@app.post("/chat", response_model=ChatResponse)
@limiter.limit(config.RATE_LIMIT_PER_TENANT)
async def chat(request: Request, req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="empty message")

    tenant_id = get_tenant_id()
    session_id = req.session_id or str(uuid.uuid4())

    try:
        memory = _load_memory(tenant_id, session_id)
    except Exception:
        logger.exception("load memory failed, starting fresh")
        memory = ChatMemory(llm=state.llm, model=config.BEDROCK_MODEL_ID)

    # Coordinator.run already catches LLM / guardrail errors and degrades to
    # SAFE_FALLBACK; this is a last-resort guard for unexpected failures.
    try:
        answer = await state.coordinator.run(req.message, memory.get_history())
    except Exception:
        logger.exception("coordinator.run crashed")
        answer = SAFE_FALLBACK

    memory.add_turn("user", req.message)
    memory.add_turn("assistant", answer)
    try:
        memory.compress_if_needed()
    except Exception:
        logger.exception("compress_if_needed failed, skipping")

    try:
        _save_memory(tenant_id, session_id, memory)
    except Exception:
        logger.exception("save memory failed; continuing to return response")

    return ChatResponse(session_id=session_id, response=answer, tenant_id=tenant_id)


@app.delete("/session/{session_id}")
def clear_session(session_id: str):
    tenant_id = get_tenant_id()
    if state.sessions is not None:
        state.sessions.delete(tenant_id, session_id)
    return {"ok": True}


# ---------- Streaming ----------

@app.post("/chat/stream")
@limiter.limit(config.RATE_LIMIT_PER_TENANT)
async def chat_stream(request: Request, req: ChatRequest):
    """SSE streaming endpoint.

    Event format:
        event: phase | token | done | error
        data:  <json string>

    Streaming path bypasses QueryCache but still persists session memory on done.
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="empty message")

    tenant_id = get_tenant_id()
    session_id = req.session_id or uuid.uuid4().hex

    try:
        memory = _load_memory(tenant_id, session_id)
    except Exception:
        logger.exception("load memory failed, starting fresh")
        memory = ChatMemory(llm=state.llm, model=config.BEDROCK_MODEL_ID)

    history = memory.get_history()

    async def _event_gen():
        yield _sse("meta", {"session_id": session_id, "tenant_id": tenant_id})
        full_answer = ""
        try:
            async for ev in state.coordinator.run_stream(req.message, history):
                yield _sse(ev["event"], ev["data"])
                if ev["event"] == "done":
                    full_answer = ev["data"].get("full", "")
                elif ev["event"] == "error":
                    full_answer = SAFE_FALLBACK
        except Exception:
            logger.exception("stream crashed")
            yield _sse("error", {"reason": "internal_error"})
            full_answer = SAFE_FALLBACK

        if full_answer:
            memory.add_turn("user", req.message)
            memory.add_turn("assistant", full_answer)
            try:
                memory.compress_if_needed()
                _save_memory(tenant_id, session_id, memory)
            except Exception:
                logger.exception("stream memory persist failed")

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


# ---------- Metrics ----------

@app.get("/metrics/tokens")
def tokens_summary():
    """Token usage and cost summary for the current tenant.

    On AWS the authoritative numbers come from CloudWatch Metrics
    (namespace ``CareAgent``). The response here is a pointer to the
    dashboard query; the in-process tracker only emits, it does not
    aggregate.
    """
    tenant_id = get_tenant_id()
    if state.tokens is None:
        return {"enabled": False}
    return state.tokens.summary(tenant_id)
