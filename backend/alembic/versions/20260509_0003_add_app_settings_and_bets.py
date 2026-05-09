"""Add app_settings and bets tables for trading feature.

Revision ID: 20260509_0003
Revises: 20260429_0002
Create Date: 2026-05-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision: str = "20260509_0003"
down_revision: Union[str, None] = "20260429_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "updated_at_utc",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    op.execute(
        sa.text("""
            INSERT INTO app_settings (key, value) VALUES
              ('betting.execution_enabled', 'false'),
              ('risk.kill_switch', 'false'),
              ('betting.time_in_force', 'FOK'),
              ('betting.slippage_tolerance', '0.05')
            ON CONFLICT (key) DO NOTHING
        """)
    )

    op.create_table(
        "bets",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("condition_id", sa.Text(), server_default="''", nullable=False),
        sa.Column("token_id", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("bucket_label", sa.Text(), nullable=False),
        sa.Column("market_title", sa.Text(), nullable=False),
        sa.Column("event_slug", sa.Text(), nullable=True),
        sa.Column("amount_usd", sa.Float(precision=53), nullable=False),
        sa.Column("theoretical_price", sa.Float(precision=53), nullable=False),
        sa.Column("actual_price", sa.Float(precision=53), nullable=True),
        sa.Column("clob_order_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="'pending'", nullable=False),
        sa.Column("pnl", sa.Float(precision=53), nullable=True),
        sa.Column("snapshot_json", JSONB(), nullable=True),
        sa.Column(
            "placed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("side IN ('yes', 'no')", name="bets_side_check"),
        sa.CheckConstraint(
            "status IN ('dry_run','pending','filled','failed','won','lost','cancelled')",
            name="bets_status_check",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("idx_bets_status", "bets", ["status"])
    op.create_index("idx_bets_placed_at", "bets", [sa.text("placed_at DESC")])
    op.create_index(
        "idx_bets_event_slug",
        "bets",
        ["event_slug"],
        postgresql_where=sa.text("event_slug IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_bets_event_slug", table_name="bets")
    op.drop_index("idx_bets_placed_at", table_name="bets")
    op.drop_index("idx_bets_status", table_name="bets")
    op.drop_table("bets")
    op.drop_table("app_settings")
