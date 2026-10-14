"""description_drafts.reviewed_by: ON DELETE SET NULL, so deleting an account keeps the
drafts it reviewed (found when the e2e clean-up deleted a test admin)

Revision ID: 0006
Revises: 0005
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK = "fk_description_drafts_reviewed_by_customers"


def upgrade() -> None:
    op.drop_constraint(FK, "description_drafts", type_="foreignkey")
    op.create_foreign_key(
        FK, "description_drafts", "customers", ["reviewed_by"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(FK, "description_drafts", type_="foreignkey")
    op.create_foreign_key(FK, "description_drafts", "customers", ["reviewed_by"], ["id"])
