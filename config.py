"""Central configuration. All runtime values come from env vars.

AWS deployment path:
    - ECS task definition injects env vars from the task definition itself and
      from Secrets Manager (DB creds).
    - Everything AWS-related (OpenSearch endpoint, DynamoDB table, Bedrock
      model IDs) is plain config; no secrets.

Local dev path:
    - `.env` file at project root is loaded by python-dotenv.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


APP_ENV = os.getenv("APP_ENV", "dev").strip().lower()
IS_PRODUCTION = APP_ENV in {"prod", "production"}


# ---------- AWS core ----------

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# ---------- Bedrock (LLM + embedding) ----------

BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-sonnet-4-20250514-v1:0",
)
BEDROCK_ROUTER_MODEL_ID = os.getenv(
    "BEDROCK_ROUTER_MODEL_ID",
    "anthropic.claude-haiku-4-5-20251001-v1:0",
)
BEDROCK_EMBEDDING_MODEL_ID = os.getenv(
    "BEDROCK_EMBEDDING_MODEL_ID",
    "amazon.titan-embed-text-v2:0",
)
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", 1024))

MAX_TOKENS = int(os.getenv("MAX_TOKENS", 2048))
MAX_STEPS = int(os.getenv("MAX_STEPS", 10))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", 30))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", 3))

# ---------- OpenSearch (dense k-NN + sparse BM25 in one index) ----------

OPENSEARCH_ENDPOINT = os.getenv("OPENSEARCH_ENDPOINT", "")
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "care-knowledge")

# ---------- RDS PostgreSQL ----------
#
# Preferred path: runtime resolves PG_SECRET_ID via Secrets Manager and assembles
# the URL from {username, password, host, port, dbname}. For local dev the
# caller can set DATABASE_URL directly and skip Secrets Manager.

PG_SECRET_ID = os.getenv("PG_SECRET_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ---------- DynamoDB (session memory) ----------

DYNAMODB_SESSION_TABLE = os.getenv("DYNAMODB_SESSION_TABLE", "care-agent-sessions")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", 60 * 60 * 24))

# ---------- Cache (no-op in AWS mode; ElastiCache was dropped) ----------

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 300))
CACHE_ENABLED = _get_bool("CACHE_ENABLED", False)

# ---------- Multi-tenant + rate limit ----------

DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "default")
RATE_LIMIT_PER_TENANT = os.getenv("RATE_LIMIT_PER_TENANT", "60/minute")
TENANT_SOURCE = os.getenv(
    "TENANT_SOURCE",
    "trusted_header" if IS_PRODUCTION else "header",
).strip().lower()
TENANT_HEADER = os.getenv("TENANT_HEADER", "X-Tenant-ID")
TRUSTED_TENANT_HEADER = os.getenv("TRUSTED_TENANT_HEADER", "X-Verified-Tenant-ID")

ALLOW_INPROC_MEMORY_FALLBACK = _get_bool("ALLOW_INPROC_MEMORY_FALLBACK", not IS_PRODUCTION)
ALLOW_LOCAL_KB_FALLBACK = _get_bool("ALLOW_LOCAL_KB_FALLBACK", not IS_PRODUCTION)

# ---------- Knowledge build script ----------

KNOWLEDGE_DOCS_DIR = PROJECT_ROOT / os.getenv("KNOWLEDGE_DOCS_DIR", "data/knowledge_docs")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 512))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 50))
