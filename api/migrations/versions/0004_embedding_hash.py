"""products.embedding_hash: which text (and model) the stored embedding was made from

Revision ID: 0004
Revises: 0003
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("embedding_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "embedding_hash")
