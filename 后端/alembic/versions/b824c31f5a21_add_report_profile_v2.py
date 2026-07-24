"""add report profile v2 fields

Revision ID: b824c31f5a21
Revises: a719304102bf
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b824c31f5a21"
down_revision: str | None = "a719304102bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("profile_version", sa.Integer(), nullable=True))
    op.add_column("reports", sa.Column("profile_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "profile_data")
    op.drop_column("reports", "profile_version")
