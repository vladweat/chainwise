"""Track the last successfully published analysis generation.

Revision ID: 011
Revises: 010
Create Date: 2026-11-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contracts",
        sa.Column("analysis_generation_id", sa.String(length=36), nullable=True),
    )
    # PR-06 used the attempt UUID as the Qdrant generation for READY rows.
    # Preserve access to those chunks while failed/in-progress rows remain gated.
    op.execute(
        "UPDATE contracts "
        "SET analysis_generation_id = indexing_attempt_id "
        "WHERE status = 'ready' AND indexing_attempt_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("contracts", "analysis_generation_id")
