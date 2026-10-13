"""How many units of a variant can be sold right now (ADR-003): the orders module's read-only
interface for stock. A leaf module (models only), so the catalog can use it without pulling
in the rest of orders, which itself depends on the cart and the catalog.
"""

from sqlalchemy import ColumnElement, func, select

from app.modules.catalog.models import Variant
from app.modules.orders.models import Reservation


def _expired_unreleased() -> ColumnElement[int]:
    """Correlated subquery: units in this variant's holds that have expired but not been
    released yet. Uses the partial index ix_reservations_active (variant_id, expires_at)."""
    return (
        select(func.coalesce(func.sum(Reservation.qty), 0))
        .where(
            Reservation.variant_id == Variant.id,
            Reservation.released_at.is_(None),
            Reservation.expires_at <= func.now(),
        )
        .scalar_subquery()
    )


def available_qty() -> ColumnElement[int]:
    """SQL expression: units of a variant that can be sold right now. Holds that have expired
    don't count, whether or not anything has released them yet."""
    return Variant.stock_on_hand - Variant.stock_reserved + _expired_unreleased()
