import asyncio
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import Customer
from app.modules.accounts.models import Session as LoginSession
from app.modules.cart.models import Cart, CartItem
from app.modules.catalog.models import Product, Variant
from tests.conftest import sign_up

PASSWORD = "correct horse"


async def test_signup_sets_httponly_session_and_hashes_with_argon2(
    client: AsyncClient, session: AsyncSession
) -> None:
    resp = await client.post(
        "/auth/signup", json={"email": "Ada@Example.com", "password": PASSWORD}
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "ada@example.com"  # stored lowercase
    assert resp.json()["role"] == "customer"
    cookie = resp.headers["set-cookie"]
    assert "session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie

    stored = await session.scalar(select(Customer).where(Customer.email == "ada@example.com"))
    assert stored is not None and stored.password_hash.startswith("$argon2id$")
    assert PASSWORD not in stored.password_hash

    me = await client.get("/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "ada@example.com"


async def test_duplicate_email_is_409_whatever_the_case(client: AsyncClient) -> None:
    email = await sign_up(client)
    resp = await client.post("/auth/signup", json={"email": email.upper(), "password": PASSWORD})
    assert resp.status_code == 409


async def test_signup_validation(client: AsyncClient) -> None:
    short = await client.post("/auth/signup", json={"email": "a@example.com", "password": "short"})
    assert short.status_code == 422
    huge = await client.post("/auth/signup", json={"email": "a@example.com", "password": "x" * 129})
    assert huge.status_code == 422
    bad = await client.post("/auth/signup", json={"email": "not-an-email", "password": PASSWORD})
    assert bad.status_code == 422


async def test_login_errors_do_not_reveal_which_emails_exist(
    client: AsyncClient, make_client
) -> None:  # type: ignore[no-untyped-def]
    email = await sign_up(make_client())
    wrong_pw = await client.post("/auth/login", json={"email": email, "password": "wrong one"})
    no_user = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "wrong one"}
    )
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()
    assert (
        await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    ).status_code == 200


async def test_logout_kills_the_session_server_side(client: AsyncClient) -> None:
    await sign_up(client)
    token = client.cookies["session"]
    assert (await client.post("/auth/logout")).status_code == 204
    assert (await client.get("/auth/me")).status_code == 401
    client.cookies.set("session", token)  # replaying the old cookie doesn't work either
    assert (await client.get("/auth/me")).status_code == 401


async def test_login_issues_a_new_token_and_drops_the_old_one(client: AsyncClient) -> None:
    email = await sign_up(client)
    first = client.cookies["session"]
    await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    second = client.cookies["session"]
    assert first != second
    client.cookies.set("session", first)
    assert (await client.get("/auth/me")).status_code == 401


async def test_expired_session_is_rejected(client: AsyncClient, session: AsyncSession) -> None:
    email = await sign_up(client)
    customer_id = await session.scalar(select(Customer.id).where(Customer.email == email))
    await session.execute(
        update(LoginSession)
        .where(LoginSession.customer_id == customer_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await session.commit()
    assert (await client.get("/auth/me")).status_code == 401


# --- carts and accounts ---------------------------------------------------------------------


async def in_stock_variants(session: AsyncSession, n: int) -> list[Variant]:
    """n variants of active products with at least 10 available (so caps, not stock, apply)."""
    return list(
        (
            await session.execute(
                select(Variant)
                .join(Product)
                .where(
                    Product.status == "active", Variant.stock_on_hand - Variant.stock_reserved >= 10
                )
                .order_by(Variant.sku)
                .limit(n)
            )
        ).scalars()
    )


async def add(client: AsyncClient, v: Variant, qty: int) -> None:
    resp = await client.post("/cart/items", json={"variant_id": str(v.id), "qty": qty})
    assert resp.status_code == 200, resp.text


def quantities(cart: dict) -> dict[str, int]:
    return {line["sku"]: line["qty"] for line in cart["items"]}


async def test_login_merges_the_guest_cart_into_the_account_cart(
    client: AsyncClient, make_client, session: AsyncSession
) -> None:  # type: ignore[no-untyped-def]
    a, b, c, d = await in_stock_variants(session, 4)

    # The account already has a cart from another device: A x1, C x1, D x6
    other_device = make_client()
    email = await sign_up(other_device)
    await add(other_device, a, 1)
    await add(other_device, c, 1)
    await add(other_device, d, 6)

    # Shopping as a guest on this device: A x2, B x1, D x7
    await add(client, a, 2)
    await add(client, b, 1)
    await add(client, d, 7)
    guest_token = client.cookies["cart_token"]

    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    # The guest cookie is cleared: that cart belongs to the account now.
    cleared = [c for c in resp.headers.get_list("set-cookie") if c.startswith("cart_token=")]
    assert len(cleared) == 1 and "Max-Age=0" in cleared[0]

    merged = (await client.get("/cart")).json()
    # Same variant in both carts adds up; D is capped at the 10-per-line limit.
    assert quantities(merged) == {a.sku: 3, b.sku: 1, c.sku: 1, d.sku: 10}
    # Both devices now see the same cart.
    assert quantities((await other_device.get("/cart")).json()) == quantities(merged)

    # The guest cart is gone, not left behind as an orphan.
    from app.modules.cart.service import _hash

    assert (
        await session.scalar(select(Cart).where(Cart.session_token == _hash(guest_token))) is None
    )


async def test_signup_keeps_the_guest_cart(client: AsyncClient, session: AsyncSession) -> None:
    (a,) = await in_stock_variants(session, 1)
    await add(client, a, 2)
    await sign_up(client)
    assert quantities((await client.get("/cart")).json()) == {a.sku: 2}


async def test_signed_in_cart_does_not_use_a_guest_cookie(
    client: AsyncClient, session: AsyncSession
) -> None:
    (a,) = await in_stock_variants(session, 1)
    email = await sign_up(client)
    resp = await client.post("/cart/items", json={"variant_id": str(a.id), "qty": 1})
    assert "cart_token" not in resp.headers.get("set-cookie", "")
    customer_id = await session.scalar(select(Customer.id).where(Customer.email == email))
    owned = await session.scalar(select(Cart).where(Cart.customer_id == customer_id))
    assert owned is not None and owned.session_token is None

    # After logout the browser is a guest again with an empty cart; the account keeps its own.
    await client.post("/auth/logout")
    assert (await client.get("/cart")).json()["items"] == []
    await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert quantities((await client.get("/cart")).json()) == {a.sku: 1}


async def test_first_adds_at_once_for_a_customer_make_one_cart(
    client: AsyncClient, session: AsyncSession
) -> None:
    a, b = await in_stock_variants(session, 2)
    email = await sign_up(client)
    results = await asyncio.gather(
        client.post("/cart/items", json={"variant_id": str(a.id), "qty": 1}),
        client.post("/cart/items", json={"variant_id": str(b.id), "qty": 1}),
    )
    assert [r.status_code for r in results] == [200, 200]
    customer_id = await session.scalar(select(Customer.id).where(Customer.email == email))
    carts = await session.scalar(
        select(func.count()).select_from(Cart).where(Cart.customer_id == customer_id)
    )
    assert carts == 1
    lines = await session.scalar(
        select(func.count()).select_from(CartItem).join(Cart).where(Cart.customer_id == customer_id)
    )
    assert lines == 2
