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

POSTGRES_URL = os.getenv(
    "POSTGRES_URL",
    "postgresql://dev:somedev@localhost:5432/health_agent",
)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "claude-opus-4-7")
# Router is a lightweight classification task; Haiku is cheaper/faster.
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", 2048))
MAX_STEPS = int(os.getenv("MAX_STEPS", 10))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", 30))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", 3))

# Exact-match query cache.
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", 300))
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "1") == "1"

# Multi-tenant + rate limit.
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
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", 60 * 60 * 24))

KNOWLEDGE_DOCS_DIR = PROJECT_ROOT / os.getenv("KNOWLEDGE_DOCS_DIR", "data/knowledge_docs")

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 512))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 50))

MILVUS_HOST = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", 19530))
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "care_knowledge")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

ES_URL = os.getenv("ES_URL", "http://localhost:9200")
ES_INDEX = os.getenv("ES_INDEX", "care_knowledge")

HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_ENDPOINT", HF_ENDPOINT)
