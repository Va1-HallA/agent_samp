"""LLM backend abstraction.

Rest of the code used to talk to ``anthropic.Anthropic`` directly. On AWS the
managed path is Bedrock Converse, which:
    - uses IAM (no API key),
    - has a different message / tool / system / stream shape,
    - speaks boto3 instead of the anthropic SDK.

Rather than sprinkle boto3 across the codebase, we expose a narrow facade:

    class LLMBackend:
        def create(*, model, system, messages, tools, max_tokens) -> LLMResponse
        def stream(*, model, system, messages, max_tokens) -> StreamContext
        def embed(*, model, text) -> list[float]

Response objects mirror the shape the rest of the code already uses:
    response.content: list[ContentBlock]   (blocks have .type ∈ {"text","tool_use"})
    response.stop_reason: "end_turn" | "tool_use" | "max_tokens" | ...
    response.usage: {input_tokens, output_tokens}

Exceptions are normalised to the ``LLMError`` family so the retry decorator in
``core.llm_client`` stays backend-agnostic:

    LLMError
     ├── RetryableLLMError     # 429, 503, 500, socket timeouts, throttling
     └── NonRetryableLLMError  # 4xx (auth, validation, access denied)
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionError as BotoConnectionError,
    ReadTimeoutError,
)

logger = logging.getLogger(__name__)


# ---------- Exceptions ----------

class LLMError(Exception):
    """Any failure from the LLM backend."""


class RetryableLLMError(LLMError):
    """Transient: throttle, 5xx, connection / timeout."""


class NonRetryableLLMError(LLMError):
    """Permanent: auth, access denied, malformed request."""


# Bedrock throttling / transient error codes that are worth retrying.
_RETRYABLE_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "InternalServerException",
    "ModelTimeoutException",
    "ModelStreamErrorException",
    "ModelErrorException",
}


def _wrap_boto_error(exc: Exception) -> LLMError:
    """Map boto errors onto the LLMError family."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _RETRYABLE_CODES:
            return RetryableLLMError(f"{code}: {exc}")
        return NonRetryableLLMError(f"{code}: {exc}")
    if isinstance(exc, (BotoConnectionError, ReadTimeoutError)):
        return RetryableLLMError(f"{type(exc).__name__}: {exc}")
    if isinstance(exc, BotoCoreError):
        return NonRetryableLLMError(f"{type(exc).__name__}: {exc}")
    return LLMError(f"{type(exc).__name__}: {exc}")


# ---------- Response types ----------

@dataclass
class ContentBlock:
    """Normalised content block.

    - type "text"     -> text field populated
    - type "tool_use" -> id / name / input populated
    """

    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: list[ContentBlock]
    stop_reason: str
    usage: dict[str, int]
    raw: dict[str, Any] = field(default_factory=dict)


class StreamContext:
    """Context manager wrapping Bedrock's ConverseStream response.

    Mirrors the bit of ``anthropic.MessageStream`` we actually use:
        with backend.stream(...) as s:
            for chunk in s.text_stream:
                ...
    """

    def __init__(self, raw_stream: Iterator[dict[str, Any]] | None):
        self._raw = raw_stream

    def __enter__(self) -> "StreamContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # boto EventStream has no close(); iterator drain is fine.
        self._raw = None

    @property
    def text_stream(self) -> Iterator[str]:
        if self._raw is None:
            return iter(())
        return self._iter_text()

    def _iter_text(self) -> Iterator[str]:
        assert self._raw is not None
        try:
            for event in self._raw:
                delta = event.get("contentBlockDelta", {}).get("delta", {})
                text = delta.get("text")
                if text:
                    yield text
        except (BotoCoreError, ClientError) as e:
            raise _wrap_boto_error(e) from e


# ---------- Abstract interface ----------

class LLMBackend(ABC):
    @abstractmethod
    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        timeout: float | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
        timeout: float | None = None,
    ) -> StreamContext: ...

    @abstractmethod
    def embed(self, *, model: str, text: str) -> list[float]: ...


# ---------- Bedrock implementation ----------

class BedrockBackend(LLMBackend):
    """Bedrock Converse / InvokeModel wrapper.

    Credentials: standard boto3 chain — ECS task role on AWS, ``~/.aws/credentials``
    or env vars locally. No API key handling.
    """

    def __init__(self, region: str, timeout: float = 30.0):
        self.region = region
        cfg = BotoConfig(
            region_name=region,
            read_timeout=timeout,
            connect_timeout=min(timeout, 10.0),
            retries={"max_attempts": 1, "mode": "standard"},
        )
        self._runtime = boto3.client("bedrock-runtime", config=cfg)

    # ----- Messages API (Converse) -----

    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        timeout: float | None = None,
    ) -> LLMResponse:
        request = self._build_converse_request(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens,
        )
        try:
            resp = self._runtime.converse(modelId=model, **request)
        except (BotoCoreError, ClientError) as e:
            raise _wrap_boto_error(e) from e
        return self._parse_converse_response(resp)

    def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
        timeout: float | None = None,
    ) -> StreamContext:
        request = self._build_converse_request(
            system=system, messages=messages, tools=None, max_tokens=max_tokens,
        )
        try:
            resp = self._runtime.converse_stream(modelId=model, **request)
        except (BotoCoreError, ClientError) as e:
            raise _wrap_boto_error(e) from e
        return StreamContext(resp.get("stream"))

    # ----- Embeddings (Titan Embed v2) -----

    def embed(self, *, model: str, text: str) -> list[float]:
        body = json.dumps({"inputText": text}).encode("utf-8")
        try:
            resp = self._runtime.invoke_model(
                modelId=model,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
        except (BotoCoreError, ClientError) as e:
            raise _wrap_boto_error(e) from e
        payload = json.loads(resp["body"].read())
        vec = payload.get("embedding") or []
        return [float(x) for x in vec]

    # ----- Shape conversion: Anthropic -> Bedrock Converse -----

    @staticmethod
    def _build_converse_request(
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        req: dict[str, Any] = {
            "messages": [_to_bedrock_message(m) for m in messages],
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if system:
            req["system"] = [{"text": system}]
        if tools:
            req["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "inputSchema": {"json": t["input_schema"]},
                        }
                    }
                    for t in tools
                ]
            }
        return req

    @staticmethod
    def _parse_converse_response(resp: dict[str, Any]) -> LLMResponse:
        out_msg = resp.get("output", {}).get("message", {})
        raw_blocks = out_msg.get("content", [])
        blocks: list[ContentBlock] = []
        for blk in raw_blocks:
            if "text" in blk:
                blocks.append(ContentBlock(type="text", text=blk["text"]))
            elif "toolUse" in blk:
                tu = blk["toolUse"]
                blocks.append(ContentBlock(
                    type="tool_use",
                    id=tu.get("toolUseId"),
                    name=tu.get("name"),
                    input=tu.get("input") or {},
                ))
            # Other block types (reasoning, image) are not used by the agents.

        stop_reason = _normalise_stop_reason(resp.get("stopReason", ""))
        usage_raw = resp.get("usage", {})
        usage = {
            "input_tokens": int(usage_raw.get("inputTokens", 0) or 0),
            "output_tokens": int(usage_raw.get("outputTokens", 0) or 0),
        }
        return LLMResponse(content=blocks, stop_reason=stop_reason, usage=usage, raw=resp)


# ---------- Message shape converter ----------

def _to_bedrock_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert one Anthropic-shaped message into a Bedrock Converse message.

    Input role stays unchanged. Content is normalised to a list of blocks.

    Supported block types on the way in:
        - str                              -> {"text": str}
        - {"type":"text", "text": ...}     -> {"text": ...}
        - {"type":"tool_use", id,name,input}
                                           -> {"toolUse": {toolUseId,name,input}}
        - {"type":"tool_result", id, content}
                                           -> {"toolResult": {...}}
        - ContentBlock (from previous turn)  same mapping as dicts above.
    """
    role = msg["role"]
    content = msg["content"]

    if isinstance(content, str):
        return {"role": role, "content": [{"text": content}]}

    out_blocks: list[dict[str, Any]] = []
    for b in content:
        out_blocks.append(_to_bedrock_block(b))
    return {"role": role, "content": out_blocks}


def _to_bedrock_block(b: Any) -> dict[str, Any]:
    # ContentBlock dataclass produced by a previous LLMResponse.
    if isinstance(b, ContentBlock):
        if b.type == "text":
            return {"text": b.text or ""}
        if b.type == "tool_use":
            return {"toolUse": {
                "toolUseId": b.id,
                "name": b.name,
                "input": b.input or {},
            }}
        raise ValueError(f"unsupported ContentBlock.type: {b.type}")

    if isinstance(b, str):
        return {"text": b}

    if not isinstance(b, dict):
        raise TypeError(f"unsupported content block: {type(b).__name__}")

    t = b.get("type")
    if t == "text":
        return {"text": b.get("text", "")}
    if t == "tool_use":
        return {"toolUse": {
            "toolUseId": b["id"],
            "name": b["name"],
            "input": b.get("input") or {},
        }}
    if t == "tool_result":
        return {"toolResult": {
            "toolUseId": b["tool_use_id"],
            "content": _tool_result_content(b.get("content")),
            "status": "error" if b.get("is_error") else "success",
        }}
    raise ValueError(f"unsupported content block type: {t!r}")


def _tool_result_content(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return [{"text": ""}]
    if isinstance(content, str):
        return [{"text": content}]
    if isinstance(content, list):
        # Already a list of Bedrock content blocks or text dicts.
        out = []
        for c in content:
            if isinstance(c, str):
                out.append({"text": c})
            elif isinstance(c, dict) and "text" in c:
                out.append({"text": c["text"]})
            else:
                out.append({"text": json.dumps(c, ensure_ascii=False, default=str)})
        return out
    return [{"text": json.dumps(content, ensure_ascii=False, default=str)}]


def _normalise_stop_reason(bedrock_stop: str) -> str:
    """Map Bedrock stopReason onto the Anthropic vocabulary the agents expect."""
    return {
        "end_turn": "end_turn",
        "tool_use": "tool_use",
        "max_tokens": "max_tokens",
        "stop_sequence": "stop_sequence",
        "guardrail_intervened": "guardrail_intervened",
        "content_filtered": "content_filtered",
    }.get(bedrock_stop, bedrock_stop or "end_turn")
