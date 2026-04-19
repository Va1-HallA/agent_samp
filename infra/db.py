"""Database access layer.

URL resolution is lazy: on AWS the credentials live in Secrets Manager and
are only fetched on first access. ``pool_pre_ping`` handles RDS idle-timeout
disconnects that happen when a Fargate task sits idle overnight.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from infra.secrets import build_database_url


_DATABASE_URL = build_database_url()

engine = create_engine(
    _DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
