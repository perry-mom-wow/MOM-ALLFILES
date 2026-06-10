"""SQLAlchemy engine + session factory.

Uses DATABASE_URL from env. Falls back to a local SQLite file at data/ea.db so
the EA can boot without Supabase credentials. Production: set DATABASE_URL to
a Supabase Postgres connection string (postgresql+psycopg://...).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_ROOT = Path(__file__).parent.parent
_LOCAL_SQLITE = _ROOT / "data" / "ea.db"
_LOCAL_SQLITE.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_LOCAL_SQLITE}")

# Railway / Supabase inject DATABASE_URL as `postgresql://...` which SQLAlchemy
# maps to psycopg2 by default. We bundle psycopg v3 instead (smaller, no
# system libpq build). Rewrite the dialect so SQLAlchemy picks the right driver.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgresql://"):]
elif DATABASE_URL.startswith("postgres://"):
    # Heroku-style legacy URL
    DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len("postgres://"):]

_engine_kwargs: dict = {"future": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine: Engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a session that commits on success, rolls back on error, always closes."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables. Idempotent. For local dev / first-run on Supabase before Alembic."""
    from state.models import Base
    Base.metadata.create_all(engine)
