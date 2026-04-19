"""Session memory store.

Replaces the old Redis-backed session hash. DynamoDB on-demand costs nothing
when idle and its TTL attribute does expiry for us; the table is the natural
fit for "per-session small JSON blob with automatic garbage collection".

Table schema (defined in Terraform):
    HASH key: session_key  (String)  — "{tenant_id}:{session_id}"
    Attributes:
        summary    : String
        messages   : String (JSON-encoded list; DynamoDB-native lists work too
                     but serialising once keeps the conversion symmetric
                     regardless of block shape)
        expires_at : Number (epoch seconds; TTL attribute)

Fallback: when ``ALLOW_INPROC_MEMORY_FALLBACK`` is on (dev default) and
DynamoDB isn't reachable at startup, we fall back to a process-local dict so
the CLI / local uvicorn still works without AWS creds.
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod

import boto3
from botocore.exceptions import BotoCoreError, ClientError

import config

logger = logging.getLogger(__name__)


def _session_key(tenant_id: str, session_id: str) -> str:
    # Tenant prefix prevents session_id collisions across tenants.
    return f"{tenant_id}:{session_id}"


class SessionStore(ABC):
    @abstractmethod
    def load(self, tenant_id: str, session_id: str) -> dict | None: ...

    @abstractmethod
    def save(self, tenant_id: str, session_id: str, payload: dict) -> None: ...

    @abstractmethod
    def delete(self, tenant_id: str, session_id: str) -> None: ...


# ---------- DynamoDB ----------

class DynamoSessionStore(SessionStore):
    def __init__(self, table_name: str | None = None, region: str | None = None, ttl_seconds: int | None = None):
        self.table_name = table_name or config.DYNAMODB_SESSION_TABLE
        self.region = region or config.AWS_REGION
        self.ttl_seconds = ttl_seconds or config.SESSION_TTL_SECONDS
        self._table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)

    def load(self, tenant_id: str, session_id: str) -> dict | None:
        key = _session_key(tenant_id, session_id)
        try:
            resp = self._table.get_item(Key={"session_key": key})
        except (BotoCoreError, ClientError):
            logger.exception("dynamo load failed for %s", key)
            return None
        item = resp.get("Item")
        if not item:
            return None
        raw_msgs = item.get("messages") or "[]"
        try:
            messages = json.loads(raw_msgs) if isinstance(raw_msgs, str) else raw_msgs
        except json.JSONDecodeError:
            logger.warning("malformed session messages for %s", key)
            messages = []
        return {
            "summary": item.get("summary", "") or "",
            "messages": messages,
        }

    def save(self, tenant_id: str, session_id: str, payload: dict) -> None:
        key = _session_key(tenant_id, session_id)
        expires_at = int(time.time()) + self.ttl_seconds
        try:
            self._table.put_item(Item={
                "session_key": key,
                "summary": payload.get("summary", "") or "",
                "messages": json.dumps(
                    payload.get("messages", []), ensure_ascii=False, default=str,
                ),
                "expires_at": expires_at,
            })
        except (BotoCoreError, ClientError):
            logger.exception("dynamo save failed for %s", key)

    def delete(self, tenant_id: str, session_id: str) -> None:
        key = _session_key(tenant_id, session_id)
        try:
            self._table.delete_item(Key={"session_key": key})
        except (BotoCoreError, ClientError):
            logger.exception("dynamo delete failed for %s", key)


# ---------- In-process fallback ----------

class InProcSessionStore(SessionStore):
    """Dev-only. Lost when the process dies; NEVER use in production."""

    def __init__(self):
        self._d: dict[str, dict] = {}

    def load(self, tenant_id: str, session_id: str) -> dict | None:
        return self._d.get(_session_key(tenant_id, session_id))

    def save(self, tenant_id: str, session_id: str, payload: dict) -> None:
        self._d[_session_key(tenant_id, session_id)] = {
            "summary": payload.get("summary", ""),
            "messages": list(payload.get("messages", [])),
        }

    def delete(self, tenant_id: str, session_id: str) -> None:
        self._d.pop(_session_key(tenant_id, session_id), None)


# ---------- Factory ----------

def build_session_store() -> SessionStore:
    """Pick DynamoDB on AWS; fall back to in-process in dev if allowed.

    The probe is intentionally cheap — a describe_table call — so that an ECS
    task with a misconfigured role fails fast rather than silently dropping
    sessions.
    """
    try:
        store = DynamoSessionStore()
        # Probe so we fail early in production on misconfigured IAM / missing table.
        boto3.client("dynamodb", region_name=store.region).describe_table(
            TableName=store.table_name,
        )
        return store
    except Exception as e:
        if not config.ALLOW_INPROC_MEMORY_FALLBACK:
            raise RuntimeError(
                f"DynamoDB session store unavailable and fallback is disabled: {e}"
            ) from e
        logger.warning(
            "DynamoDB session store unavailable (%s); using in-process fallback",
            e,
        )
        return InProcSessionStore()
