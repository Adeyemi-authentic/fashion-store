"""Shopping assistant: message order (seq), a 'system' role for price-check corrections,
and the per-day spend table behind the daily spend cap

Revision ID: 0007
Revises: 0006
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CK = "ck_assistant_messages_role_valid"


def upgrade() -> None:
    op.add_column(
        "assistant_messages",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )
    op.create_unique_constraint(op.f("uq_assistant_messages_seq"), "assistant_messages", ["seq"])
    op.drop_constraint(op.f(CK), "assistant_messages", type_="check")
    op.create_check_constraint(
        op.f(CK), "assistant_messages", "role IN ('user', 'assistant', 'tool', 'system')"
    )
    op.create_table(
        "assistant_spend",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_assistant_spend")),
    )


def downgrade() -> None:
    op.drop_table("assistant_spend")
    op.execute("DELETE FROM assistant_messages WHERE role = 'system'")
    op.drop_constraint(op.f(CK), "assistant_messages", type_="check")
    op.create_check_constraint(
        op.f(CK), "assistant_messages", "role IN ('user', 'assistant', 'tool')"
    )
    op.drop_constraint(op.f("uq_assistant_messages_seq"), "assistant_messages", type_="unique")
    op.drop_column("assistant_messages", "seq")
