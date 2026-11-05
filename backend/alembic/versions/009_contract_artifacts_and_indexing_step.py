"""Move source/ast/slither blobs to contract_artifacts; add indexing_step.

Revision ID: 009
Revises: 008
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contract_artifacts",
        sa.Column("contract_id", sa.Integer(), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("ast", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("slither_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("contract_id"),
    )
    op.execute(
        """
        INSERT INTO contract_artifacts (contract_id, source_code, ast, slither_report)
        SELECT id, source_code, ast, slither_report FROM contracts
        """
    )
    op.add_column("contracts", sa.Column("indexing_step", sa.String(length=32), nullable=True))
    op.drop_column("contracts", "source_code")
    op.drop_column("contracts", "ast")
    op.drop_column("contracts", "slither_report")


def downgrade() -> None:
    op.add_column(
        "contracts",
        sa.Column("slither_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "contracts",
        sa.Column("ast", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "contracts",
        sa.Column("source_code", sa.Text(), nullable=False, server_default=""),
    )
    op.execute(
        """
        UPDATE contracts c SET
          source_code = a.source_code,
          ast = a.ast,
          slither_report = a.slither_report
        FROM contract_artifacts a
        WHERE a.contract_id = c.id
        """
    )
    op.alter_column("contracts", "source_code", server_default=None)
    op.drop_column("contracts", "indexing_step")
    op.drop_table("contract_artifacts")
