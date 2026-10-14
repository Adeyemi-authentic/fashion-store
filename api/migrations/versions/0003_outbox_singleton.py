"""outbox_jobs.singleton_key: at most one queued/running copy of a scheduled job

Revision ID: 0003
Revises: 0002
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("outbox_jobs", sa.Column("singleton_key", sa.Text(), nullable=True))
    op.create_index(
        "uq_outbox_jobs_singleton_active",
        "outbox_jobs",
        ["singleton_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_outbox_jobs_singleton_active", table_name="outbox_jobs")
    op.drop_column("outbox_jobs", "singleton_key")
