"""Add Polymarket real resolution columns to markets.

Revision ID: 20260427_0001
Revises:
Create Date: 2026-04-27

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260427_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == name for c in insp.get_columns(table))


def upgrade() -> None:
    if not _column_exists("markets", "pm_resolved_at_utc"):
        op.add_column("markets", sa.Column("pm_resolved_at_utc", sa.TIMESTAMP(timezone=True), nullable=True))
    if not _column_exists("markets", "pm_winning_label"):
        op.add_column("markets", sa.Column("pm_winning_label", sa.Text(), nullable=True))
    if not _column_exists("markets", "pm_winning_bucket_index"):
        op.add_column("markets", sa.Column("pm_winning_bucket_index", sa.Integer(), nullable=True))
    if not _column_exists("markets", "pm_resolution_checked_at_utc"):
        op.add_column("markets", sa.Column("pm_resolution_checked_at_utc", sa.TIMESTAMP(timezone=True), nullable=True))
    if not _column_exists("markets", "pm_resolution_meta"):
        op.add_column(
            "markets",
            sa.Column("pm_resolution_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    for col in (
        "pm_resolution_meta",
        "pm_resolution_checked_at_utc",
        "pm_winning_bucket_index",
        "pm_winning_label",
        "pm_resolved_at_utc",
    ):
        if _column_exists("markets", col):
            op.drop_column("markets", col)
