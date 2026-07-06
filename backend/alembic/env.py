"""Alembic environment for ClarionPI.

The database URL comes from the ``DATABASE_URL`` env var, defaulting to a local SQLite
file for offline dev. ``target_metadata`` is ``app.models.orm.Base.metadata`` so
autogenerate and the migration/model-drift test see the full schema.
"""

from __future__ import annotations

import os

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.models.orm import Base

# Alembic Config object — access to values in alembic.ini.
config = context.config

# Resolve the URL at runtime (env.py owns this, not alembic.ini).
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./clarionpi_dev.db")
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL against a URL, no live DBAPI."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — against a live Engine connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
