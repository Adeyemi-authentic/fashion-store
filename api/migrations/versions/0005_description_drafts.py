"""description_drafts (LLM-drafted product descriptions awaiting an admin) and
products.description_source (who wrote the live description)

Revision ID: 0005
Revises: 0004
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("description_source", sa.Text(), server_default="template", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_products_description_source_valid"),
        "products",
        "description_source IN ('template', 'admin', 'ai_approved')",
    )
    op.create_table(
        "description_drafts",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("checks", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reviewed_by", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('needs_review', 'failed_checks', 'approved', 'rejected', 'superseded')",
            name=op.f("ck_description_drafts_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_description_drafts_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["customers.id"],
            name=op.f("fk_description_drafts_reviewed_by_customers"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_description_drafts")),
    )
    op.create_index(
        "uq_description_drafts_one_open",
        "description_drafts",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('needs_review', 'failed_checks')"),
    )
    op.create_index(
        "ix_description_drafts_status_created", "description_drafts", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_description_drafts_status_created", table_name="description_drafts")
    op.drop_index("uq_description_drafts_one_open", table_name="description_drafts")
    op.drop_table("description_drafts")
    op.drop_constraint(op.f("ck_products_description_source_valid"), "products", type_="check")
    op.drop_column("products", "description_source")
