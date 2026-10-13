import asyncio
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.modules.orders.models import ORDER_STATUSES, Order
from app.modules.orders.state import ALLOWED, InvalidTransition, lock_order, transition

EDGES = {(a, b) for a, targets in ALLOWED.items() for b in targets}


def test_every_status_is_in_the_machine() -> None:
    assert set(ALLOWED) == set(ORDER_STATUSES)
    assert all(t in ORDER_STATUSES for targets in ALLOWED.values() for t in targets)


async def make_order(session: AsyncSession, status: str) -> uuid.UUID:
    order = Order(
        guest_email="state@example.com",
        status=status,
        total_kobo=100,
        paystack_reference=f"test-{uuid.uuid4()}",
    )
    session.add(order)
    await session.commit()
    return order.id


@pytest.mark.parametrize("current", ORDER_STATUSES)
@pytest.mark.parametrize("wanted", ORDER_STATUSES)
async def test_only_diagram_moves_are_allowed(
    session: AsyncSession, current: str, wanted: str
) -> None:
    order_id = await make_order(session, current)
    try:
        order = await lock_order(session, order_id)
        if (current, wanted) in EDGES:
            await transition(session, order, wanted)
            await session.commit()
            assert (await lock_order(session, order_id)).status == wanted
        else:
            with pytest.raises(InvalidTransition):
                await transition(session, order, wanted)
            await session.rollback()
            assert (await lock_order(session, order_id)).status == current
    finally:
        await session.rollback()
        await session.execute(delete(Order).where(Order.id == order_id))
        await session.commit()


async def test_racing_transitions_one_wins_the_other_sees_its_result(session: AsyncSession) -> None:
    """Webhook says paid, expiry job says expired, at the same moment. The row lock makes them
    take turns: the second one sees the first one's status and must make a valid move from
    there (expired -> paid is a late payment; paid -> expired is refused)."""
    order_id = await make_order(session, "pending_payment")
    results: dict[str, str] = {}

    async def attempt(to: str, delay: float) -> None:
        async with get_sessionmaker()() as s:
            order = await lock_order(s, order_id)
            await asyncio.sleep(delay)  # hold the lock a moment, so the other one waits
            try:
                await transition(s, order, to)
                await s.commit()
                results[to] = "ok"
            except InvalidTransition:
                await s.rollback()
                results[to] = "refused"

    try:
        await asyncio.gather(attempt("expired", 0.2), attempt("paid", 0.0))
        final = (await lock_order(session, order_id)).status
        await session.rollback()
        # Either order of arrival ends in a state reachable through valid moves only.
        assert (results, final) in [
            ({"expired": "ok", "paid": "ok"}, "paid"),  # expired first, then late payment
            ({"paid": "ok", "expired": "refused"}, "paid"),  # paid first; expiry refused
        ]
    finally:
        await session.execute(delete(Order).where(Order.id == order_id))
        await session.commit()
