"""
Alembic environment.

Reads the database URL from `config.settings` rather than alembic.ini so
credentials stay out of version control and there is a single source of
truth shared with the application.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import models  # noqa: F401  (registers every model on db.metadata)
from config import settings

# Import the models package so `db.metadata` is fully populated for
# autogenerate. Importing app.py instead would boot the whole Flask app.
from models import db

alembic_config = context.config

if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

alembic_config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = db.metadata

# Flask-Session creates and owns this table at runtime, so Alembic must
# not try to drop it during autogenerate.
EXCLUDED_TABLES = {"sessions"}


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    if type_ == "table" and name in EXCLUDED_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Catch column type drift, not just added/removed columns.
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
