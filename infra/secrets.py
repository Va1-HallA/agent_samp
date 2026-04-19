"""Secrets Manager helpers.

Centralises the Secrets Manager calls so the rest of the code can stay
declarative. The only secret we fetch today is the RDS credential bundle
(username, password, host, port, dbname), but the helper is generic.

RDS Secrets Manager secrets follow a standard JSON shape when you let AWS
rotate them for you:

    {
      "engine":   "postgres",
      "host":     "care-agent-rds.xxxx.us-east-1.rds.amazonaws.com",
      "port":     5432,
      "username": "careagent",
      "password": "***",
      "dbname":   "careagent"
    }

That is the shape we expect in ``build_database_url``.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from urllib.parse import quote_plus

import boto3
from botocore.exceptions import BotoCoreError, ClientError

import config

logger = logging.getLogger(__name__)


class SecretsError(RuntimeError):
    """Raised when a secret cannot be fetched or decoded."""


@lru_cache(maxsize=16)
def get_secret_json(secret_id: str, region: str | None = None) -> dict:
    """Fetch + parse a Secrets Manager secret. Cached per process."""
    region = region or config.AWS_REGION
    client = boto3.client("secretsmanager", region_name=region)
    try:
        resp = client.get_secret_value(SecretId=secret_id)
    except (BotoCoreError, ClientError) as e:
        raise SecretsError(f"get_secret_value({secret_id}) failed: {e}") from e

    raw = resp.get("SecretString")
    if raw is None:
        raise SecretsError(f"secret {secret_id} has no SecretString")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SecretsError(f"secret {secret_id} is not JSON: {e}") from e


def build_database_url(secret_id: str | None = None, region: str | None = None) -> str:
    """Resolve DATABASE_URL.

    Order:
        1. If ``DATABASE_URL`` is explicitly set in env (local dev), return it.
        2. Otherwise fetch the Secrets Manager bundle and assemble the URL.
    """
    if config.DATABASE_URL:
        return config.DATABASE_URL

    secret_id = secret_id or config.PG_SECRET_ID
    if not secret_id:
        raise SecretsError(
            "neither DATABASE_URL nor PG_SECRET_ID is configured"
        )

    data = get_secret_json(secret_id, region=region)
    try:
        user = quote_plus(str(data["username"]))
        pwd = quote_plus(str(data["password"]))
        host = data["host"]
        port = data.get("port", 5432)
        dbname = data.get("dbname") or data.get("database") or "postgres"
    except KeyError as e:
        raise SecretsError(f"RDS secret is missing field: {e}") from e

    # ``sslmode=require`` so the driver negotiates TLS to RDS.
    return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{dbname}?sslmode=require"
