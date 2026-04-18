"""Request-scoped tenant_id via ContextVar.

Set at the FastAPI middleware; read anywhere downstream (coordinator, agent,
tool, service). ContextVar is copied across asyncio.to_thread / gather so
worker threads and subtasks inherit the value.
"""
from contextvars import ContextVar

import config

_tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default=config.DEFAULT_TENANT_ID)


def get_tenant_id() -> str:
    return _tenant_id_var.get()


def set_tenant_id(tenant_id: str) -> None:
    _tenant_id_var.set(tenant_id or config.DEFAULT_TENANT_ID)
