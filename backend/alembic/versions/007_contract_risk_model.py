"""Add contracts.risk_model and features_version for honest scoring.

Revision ID: 007
Revises: 006
Create Date: 2026-09-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contracts", sa.Column("risk_model", sa.String(length=20), nullable=True))
    op.add_column("contracts", sa.Column("features_version", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("contracts", "features_version")
    op.drop_column("contracts", "risk_model")
