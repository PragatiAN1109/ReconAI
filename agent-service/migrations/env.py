"""Alembic environment for the Investigation Service.

The database URL comes from application settings rather than alembic.ini, so
migrations and the running service can never drift onto different databases.
"""

import asyncio
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.models import SCHEMA, Base

config = context.config
if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would silence every
    # logger already configured by whatever is running the migration — the
    # application, or a test process. Migrations should configure their own
    # output, not switch off everyone else's.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Default to the application's own configuration, so migrations and the running
# service cannot drift onto different databases. A URL set explicitly — by a
# test pointing at a throwaway container, for instance — wins.
if not config.get_main_option("sqlalchemy.url", None):
    database_url = Settings().database_url
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Restrict autogenerate to this service's own schema.

    The financial core's tables live in "public" in the same database. Without
    this filter, autogenerate would see them as unknown objects and happily
    propose dropping them.
    """
    if type_ == "table":
        return obj.schema == SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    # Alembic's version table lives in this service's schema, so the schema has
    # to exist before Alembic can record anything — including before the very
    # first migration, which is what creates it for the tables.
    connection.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        # Alembic's own bookkeeping table belongs to this service too, so it
        # sits in this service's schema rather than next to Flyway's.
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
