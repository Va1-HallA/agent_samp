"""Dev-only quick DB reset:

    python -m scripts.init_db

Production must use `alembic upgrade head`.
"""
import config
from infra.db import engine
from infra.models import Base


def init():
    if config.IS_PRODUCTION:
        raise RuntimeError("scripts.init_db is disabled in production; use alembic upgrade head")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("tables created:", list(Base.metadata.tables.keys()))


if __name__ == "__main__":
    init()
