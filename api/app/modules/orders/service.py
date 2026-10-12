"""Public interface of the orders module. Other modules import from here only (ADR-001)."""

from app.modules.orders.inventory import available_qty

__all__ = ["available_qty"]
