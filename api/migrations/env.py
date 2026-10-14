import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from app.config import get_settings
from app.db import make_engine
from app.models import metadata

config = context.config
if config.config_file_name is not None:
    # Keep loggers that already exist (the app's, when migrations run in-process, e.g. tests).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def database_url() -> str:
    # Tests pass the URL explicitly; normal runs use the direct (unpooled) Neon URL.
    return config.attributes.get("database_url") or get_settings().migrations_database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = make_engine(database_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:  # called from inside a running event loop (tests)
        do_run_migrations(connectable)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
