"""Test setup: a throwaway local Postgres 16 + pgvector (via `pgserver`, no Docker needed).

One server per test session. The schema is built by running the real Alembic migrations, so
every test run also tests the migrations. The catalog is seeded once; tests that change data
put it back.
"""

import os
import tempfile
import urllib.parse
from collections.abc import AsyncIterator, Iterator

import pgserver
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _url(server: pgserver.PostgresServer, dbname: str) -> str:
    socket_dir = server.get_uri().split("host=")[1]
    return f"postgresql+psycopg://postgres@/{dbname}?host={urllib.parse.quote(socket_dir)}"


def alembic_config(url: str) -> Config:
    cfg = Config(os.path.join(API_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(API_DIR, "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


@pytest.fixture(scope="session")
def pg_server() -> Iterator[pgserver.PostgresServer]:
    data_dir = tempfile.mkdtemp(prefix="flyfit-pg-")
    server = pgserver.get_server(data_dir, cleanup_mode="delete")
    yield server
    server.cleanup()


@pytest.fixture(scope="session")
def make_database(pg_server: pgserver.PostgresServer):  # type: ignore[no-untyped-def]
    def _make(name: str) -> str:
        pg_server.psql(f"DROP DATABASE IF EXISTS {name};")
        pg_server.psql(f"CREATE DATABASE {name};")
        return _url(pg_server, name)

    return _make


@pytest.fixture(scope="session")
def database_url(make_database) -> str:  # type: ignore[no-untyped-def]
    url = make_database("flyfit_test")
    command.upgrade(alembic_config(url), "head")
    os.environ["DATABASE_URL"] = url
    os.environ["DATABASE_URL_DIRECT"] = url
    from app.config import get_settings

    get_settings.cache_clear()
    return url


@pytest.fixture(scope="session")
async def seeded(database_url: str) -> AsyncIterator[int]:
    """Number of active (visible) products after seeding."""
    from app.db import dispose_engine, get_sessionmaker
    from scripts.seed_catalog import seed

    async with get_sessionmaker()() as session:
        result = await seed(session)
    yield result.active
    await dispose_engine()


@pytest.fixture
async def session(seeded: int) -> AsyncIterator[AsyncSession]:
    from app.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        yield s


@pytest.fixture
async def client(seeded: int) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
