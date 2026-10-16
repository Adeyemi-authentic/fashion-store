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

# Test modules import app.main, which builds the app and reads settings, before the test
# database exists. Point settings at a database that can't be reached until `database_url`
# below sets the real one, so tests never read (or reach) the database in .env, and run the
# same in CI, where there is no .env. Environment variables win over .env.
for _key in ("DATABASE_URL", "DATABASE_URL_DIRECT"):
    os.environ[_key] = "postgresql+psycopg://unused@invalid.localhost/flyfit_not_connected"
for _key in ("VOYAGE_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ[_key] = ""


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
    # Never call the real AI APIs from tests, even with keys in .env: empty keys select the
    # local hash embedder and switch LLM calls off. Tests that need an LLM install a fake.
    os.environ["VOYAGE_API_KEY"] = ""
    os.environ["ANTHROPIC_API_KEY"] = ""
    from app.config import get_settings

    get_settings.cache_clear()
    return url


@pytest.fixture(scope="session")
async def seeded(database_url: str) -> AsyncIterator[int]:
    """Number of active (visible) products after seeding."""
    from app.db import dispose_engine, get_sessionmaker
    from app.modules.search.embeddings import refresh_embeddings
    from scripts.seed_catalog import seed

    async with get_sessionmaker()() as session:
        result = await seed(session)
        await refresh_embeddings(session)
        await session.commit()
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


async def sign_up(client: AsyncClient, role: str = "customer") -> str:
    """Sign a new account in on `client` (it keeps the session cookie). Returns the email."""
    import uuid

    from app.db import get_sessionmaker
    from app.modules.accounts.service import set_role

    email = f"user-{uuid.uuid4().hex[:10]}@example.com"
    resp = await client.post("/auth/signup", json={"email": email, "password": "correct horse"})
    assert resp.status_code == 201, resp.text
    if role != "customer":
        async with get_sessionmaker()() as s:
            await set_role(s, email, role)
            await s.commit()
    return email


@pytest.fixture
async def make_client(seeded: int) -> AsyncIterator:  # type: ignore[type-arg]
    """Extra independent clients (separate cookie jars), e.g. an admin and a customer."""
    from app.main import create_app

    app = create_app()
    clients: list[AsyncClient] = []

    def _make() -> AsyncClient:
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()


@pytest.fixture
def paystack(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Every request in the test talks to an in-memory Paystack."""
    from app.modules.payments import paystack as module
    from app.modules.payments.fake import FakePaystack

    fake = FakePaystack()
    monkeypatch.setattr(module, "_client", fake)
    return fake


@pytest.fixture
async def stocked(session: AsyncSession):  # type: ignore[no-untyped-def]
    """Two variants of active products with exactly 5 available each (nothing reserved).
    Afterwards: orders touching them are deleted (with their holds, keys and jobs) and the
    stock is put back. Fails the test if stock_reserved drifted from the reservations."""
    from sqlalchemy import delete, select, update

    from app.db import get_sessionmaker
    from app.modules.catalog.models import Product, Variant
    from app.modules.orders import inventory
    from app.modules.orders.models import IdempotencyKey, Order, OrderItem
    from app.modules.payments.models import PaymentEvent
    from app.platform.models import OutboxJob

    variants = list(
        (
            await session.execute(
                select(Variant)
                .join(Product)
                .where(Product.status == "active")
                .order_by(Variant.sku)
                .offset(70)
                .limit(2)
            )
        ).scalars()
    )
    saved = {v.id: (v.stock_on_hand, v.stock_reserved) for v in variants}
    await session.execute(
        update(Variant).where(Variant.id.in_(saved)).values(stock_on_hand=5, stock_reserved=0)
    )
    await session.commit()
    session.expunge_all()
    yield variants
    await session.rollback()
    orders = select(OrderItem.order_id).where(OrderItem.variant_id.in_(saved))
    await session.execute(delete(IdempotencyKey).where(IdempotencyKey.order_id.in_(orders)))
    await session.execute(delete(OutboxJob))
    await session.execute(delete(PaymentEvent))
    await session.execute(delete(Order).where(Order.id.in_(orders)))
    for vid, (on_hand, reserved) in saved.items():
        await session.execute(
            update(Variant)
            .where(Variant.id == vid)
            .values(stock_on_hand=on_hand, stock_reserved=reserved)
        )
    await session.commit()
    async with get_sessionmaker()() as s:
        assert await inventory.reserved_mismatches(s) == []


class FakeEmail:
    """Behaves like Resend: a second send with the same idempotency key delivers nothing."""

    def __init__(self) -> None:
        self.calls: list[str] = []  # every send attempt's key
        self.delivered: dict[str, object] = {}  # key -> Email actually delivered

    async def send(self, email, idempotency_key: str) -> None:  # type: ignore[no-untyped-def]
        self.calls.append(idempotency_key)
        self.delivered.setdefault(idempotency_key, email)

    def to(self, address: str) -> list:  # type: ignore[type-arg]
        return [e for e in self.delivered.values() if e.to == address]  # type: ignore[attr-defined]


@pytest.fixture
def outbox_email(monkeypatch: pytest.MonkeyPatch) -> FakeEmail:
    from app.platform import email as module
    from app.platform.jobs import register_all

    register_all()
    fake = FakeEmail()
    monkeypatch.setattr(module, "_sender", fake)
    return fake


async def paid_order(
    client: AsyncClient, paystack, variant, qty: int = 1, email: str = "buyer@example.com"
) -> dict:  # type: ignore[no-untyped-def]
    """Checkout + pay through the callback path (Paystack fake). Returns the checkout response."""
    import uuid as _uuid

    await client.post("/cart/items", json={"variant_id": str(variant.id), "qty": qty})
    out = (
        await client.post(
            "/checkout", json={"email": email}, headers={"Idempotency-Key": f"k-{_uuid.uuid4()}"}
        )
    ).json()
    paystack.pay(out["reference"])
    assert (await client.get(f"/orders/by-reference/{out['reference']}")).json()["status"] == "paid"
    return out


@pytest.fixture(autouse=True)
def generous_rate_limits(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Every test client comes from one 'IP', and some tests check out a dozen times in a
    minute. Limits are raised for all tests except the rate-limit tests themselves."""
    if request.node.get_closest_marker("real_rate_limits"):
        return
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_checkout_per_min", 10_000)
    monkeypatch.setattr(get_settings(), "rate_limit_login_per_min", 10_000)
    monkeypatch.setattr(get_settings(), "rate_limit_search_per_min", 10_000)
    monkeypatch.setattr(get_settings(), "rate_limit_assistant_per_min", 10_000)


@pytest.fixture(autouse=True)
def empty_catalog_cache() -> None:
    """The catalog cache lives in the process; one test's cached reads must not leak into
    the next test's view of the data."""
    from app.modules.search.service import query_vectors
    from app.modules.search.understand import understood_cache
    from app.platform.cache import catalog_cache

    catalog_cache.clear()
    query_vectors.clear()
    understood_cache.clear()
