"""Add durable attempt identity and worker claim/heartbeat fields.

Revision ID: 010
Revises: 009
Create Date: 2026-11-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contracts", sa.Column("indexing_attempt_id", sa.String(length=36), nullable=True))
    op.add_column(
        "contracts",
        sa.Column("indexing_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contracts",
        sa.Column("indexing_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_contracts_status_indexing_heartbeat",
        "contracts",
        ["status", "indexing_heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_contracts_status_indexing_heartbeat", table_name="contracts")
    op.drop_column("contracts", "indexing_claimed_at")
    op.drop_column("contracts", "indexing_heartbeat_at")
    op.drop_column("contracts", "indexing_attempt_id")
