"""Database engine, session handling, and datetime convention.

DATETIME CONVENTION (critical, shared by all layers):
    - All datetimes stored in the database are NAIVE UTC.
    - `utcnow()` returns `datetime.now(timezone.utc).replace(tzinfo=None)`.
    - Every model column default uses `utcnow()`.
    - Every service that compares datetimes uses `utcnow()` as "now".
    - Never persist tz-aware datetimes; if one arrives from the edge
      (e.g. a parsed ISO timestamp), convert with
      `dt.astimezone(timezone.utc).replace(tzinfo=None)` before storing.
    - Rationale: SQLite has no tz-aware DateTime support, and Postgres
      `timestamp without time zone` matches naive semantics; staying naive
      everywhere keeps the code portable across both.
"""

from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


def utcnow() -> datetime:
    """Current time as a naive UTC datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# Sync engine only. SQLite check_same_thread=False allows a single shared
# connection across threads (used by FastAPI dependency injection).
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, commits nothing, closes always."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables from model metadata (used in dev; prod uses alembic)."""
    Base.metadata.create_all(bind=engine)
