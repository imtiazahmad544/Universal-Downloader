"""Alembic environment: model metadata comes from app.models (Base.metadata);
the database URL comes from app.core.config (DATABASE_URL env var / .env)."""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

# Ensure `app` is importable when alembic runs from server/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connect_args = (
        {"check_same_thread": False} if get_url().startswith("sqlite") else {}
    )
    engine = create_engine(get_url(), connect_args=connect_args, pool_pre_ping=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
