"""Allow pm_resolved market status.

Revision ID: 20260429_0002
Revises: 20260427_0001
Create Date: 2026-04-29
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260429_0002"
down_revision: Union[str, None] = "20260427_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _constraint_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == name for c in insp.get_check_constraints(table))


def upgrade() -> None:
    if _constraint_exists("markets", "markets_status_check"):
        op.drop_constraint("markets_status_check", "markets", type_="check")
    op.create_check_constraint(
        "markets_status_check",
        "markets",
        "status IN ('tracking', 'nominally_resolved', 'pm_resolved')",
    )


def downgrade() -> None:
    op.execute("UPDATE markets SET status = 'nominally_resolved' WHERE status = 'pm_resolved'")
    if _constraint_exists("markets", "markets_status_check"):
        op.drop_constraint("markets_status_check", "markets", type_="check")
    op.create_check_constraint(
        "markets_status_check",
        "markets",
        "status IN ('tracking', 'nominally_resolved')",
    )
