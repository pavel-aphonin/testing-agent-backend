"""Alembic environment — async-aware, reads DB URL from app settings.

The backend runs on asyncpg and we don't want a second, sync-only
driver in the image just for migrations. The pattern below follows
Alembic's "async recipes" cookbook: build an AsyncEngine, open a
single connection, run migrations synchronously against it via
`connection.run_sync(do_run_migrations)`.

`settings.database_url` is the single source of truth for every
piece of the backend that talks to Postgres — CRUD, lifespan,
migrations — so the `sqlalchemy.url` in alembic.ini is intentionally
empty and gets overridden here at load time.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.config import settings
from app.db import Base

# Importing app.models registers every ORM class on Base.metadata.
# Without this import autogenerate would think the schema is empty
# and produce a no-op migration.
import app.models  # noqa: F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Override the (blank) sqlalchemy.url from alembic.ini with the one
# coming from app settings so we have exactly one DB URL to configure.
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine and associate a
    connection with the context."""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well.  By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
