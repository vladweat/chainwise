"""Cache citations on analysis_reports; unique (contract_id, question_hash).

Revision ID: 005
Revises: 004
Create Date: 2026-09-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_reports",
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_unique_constraint(
        "uq_analysis_reports_contract_question",
        "analysis_reports",
        ["contract_id", "question_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_analysis_reports_contract_question",
        "analysis_reports",
        type_="unique",
    )
    op.drop_column("analysis_reports", "citations")
