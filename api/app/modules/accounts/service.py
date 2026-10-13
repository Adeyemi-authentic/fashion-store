"""Customers, password hashing and login sessions. Public interface of the accounts module.

Sessions are server-side: the cookie holds a random token, the `sessions` table holds only its
sha256. Logging out or expiring a session is a row delete/check, not a hope that a JWT expires.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from anyio import to_thread
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import Customer, Session

SESSION_DAYS = 30

# argon2id with the library defaults (RFC 9106 "low memory": 64 MiB, 3 passes).
_hasher = PasswordHasher()
# Verified against when the email doesn't exist, so a wrong email takes as long as a wrong
# password and response time doesn't reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class EmailTaken(Exception):
    pass


@dataclass(frozen=True)
class CurrentCustomer:
    id: uuid.UUID
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def hash_password(password: str) -> str:
    # ~50 ms of CPU: run it on a worker thread so other requests keep being served.
    return await to_thread.run_sync(_hasher.hash, password)


async def _verify(password_hash: str, password: str) -> bool:
    def check() -> bool:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    return await to_thread.run_sync(check)


async def create_customer(session: AsyncSession, email: str, password: str) -> Customer:
    customer = Customer(email=email.lower(), password_hash=await hash_password(password))
    session.add(customer)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailTaken(email) from exc
    return customer


async def authenticate(session: AsyncSession, email: str, password: str) -> Customer | None:
    customer = (
        await session.execute(select(Customer).where(Customer.email == email.lower()))
    ).scalar_one_or_none()
    if customer is None:
        await _verify(_DUMMY_HASH, password)
        return None
    if not await _verify(customer.password_hash, password):
        return None
    if _hasher.check_needs_rehash(customer.password_hash):
        # Hashing parameters were raised since this hash was made: upgrade it now that we
        # have the plain password.
        customer.password_hash = await hash_password(password)
    return customer


async def start_session(session: AsyncSession, customer_id: uuid.UUID) -> str:
    """Create a login session and return its token (for the cookie). Not committed."""
    token = secrets.token_urlsafe(32)
    session.add(
        Session(
            token_hash=_hash_token(token),
            customer_id=customer_id,
            expires_at=datetime.now(UTC) + timedelta(days=SESSION_DAYS),
        )
    )
    await session.flush()
    return token


async def end_session(session: AsyncSession, token: str | None) -> None:
    if token:
        await session.execute(delete(Session).where(Session.token_hash == _hash_token(token)))


async def current_customer(session: AsyncSession, token: str | None) -> CurrentCustomer | None:
    if not token:
        return None
    row = (
        await session.execute(
            select(Customer.id, Customer.email, Customer.role)
            .join(Session, Session.customer_id == Customer.id)
            .where(Session.token_hash == _hash_token(token), Session.expires_at > func.now())
        )
    ).one_or_none()
    return CurrentCustomer(*row) if row else None


async def emails_for(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(select(Customer.id, Customer.email).where(Customer.id.in_(ids)))
    return {row.id: row.email for row in rows}


async def set_role(session: AsyncSession, email: str, role: str) -> bool:
    """Only used by scripts/make_admin.py. There is deliberately no API route for this."""
    result = await session.execute(
        update(Customer).where(Customer.email == email.lower()).values(role=role)
    )
    return result.rowcount > 0  # type: ignore[attr-defined]
