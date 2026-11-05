"""Per-user contract uniqueness and CASCADE ownership.

Revision ID: 008
Revises: 007
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Orphans were readable by any logged-in user; drop before NOT NULL.
    op.execute("DELETE FROM contracts WHERE user_id IS NULL")

    op.drop_constraint("uq_contracts_address_chain", "contracts", type_="unique")
    op.drop_constraint("contracts_user_id_fkey", "contracts", type_="foreignkey")

    op.alter_column(
        "contracts",
        "user_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "contracts_user_id_fkey",
        "contracts",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_contracts_user_address_chain",
        "contracts",
        ["user_id", "address", "chain_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_contracts_user_address_chain", "contracts", type_="unique")
    op.drop_constraint("contracts_user_id_fkey", "contracts", type_="foreignkey")
    op.alter_column(
        "contracts",
        "user_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_foreign_key(
        "contracts_user_id_fkey",
        "contracts",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # May fail if duplicate (address, chain_id) rows exist across users.
    op.create_unique_constraint(
        "uq_contracts_address_chain",
        "contracts",
        ["address", "chain_id"],
    )
