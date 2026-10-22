"""Alembic environment. Migrations run as the schema-owner role (CB_MIGRATION_DATABASE_URL)."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  - registers tables
from app.db import Base

target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("CB_MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("CB_MIGRATION_DATABASE_URL must be set to run migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
