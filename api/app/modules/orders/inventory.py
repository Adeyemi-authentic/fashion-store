"""The only place stock columns are read for availability or written (ADR-003).

Phase 1 only needs the read side. Phase 4 adds holds, releases and the late-payment path, and
changes `available_qty` to ignore holds whose expires_at has passed (expiry checked on read).
"""

from sqlalchemy import ColumnElement

from app.modules.catalog.models import Variant


def available_qty() -> ColumnElement[int]:
    """SQL expression: units of a variant that can be sold right now."""
    return Variant.stock_on_hand - Variant.stock_reserved
