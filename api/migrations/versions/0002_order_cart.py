"""orders.cart_id: which cart an order came from, so paid items can be removed from it

Revision ID: 0002
Revises: 0001
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("cart_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_orders_cart_id_carts"), "orders", "carts", ["cart_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_orders_cart_id_carts"), "orders", type_="foreignkey")
    op.drop_column("orders", "cart_id")
