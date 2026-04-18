"""Database access layer. Exposes module-level `engine` and `SessionLocal`."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config


engine = create_engine(config.POSTGRES_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
