"""paper trading: account, strategies, positions, fills, event log, equity history

Revision ID: 0005_paper
Revises: 0004_13f
Create Date: 2026-10-10

Stage 1 of the automated-trading plan: simulated fills on live quotes, no broker connection.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_paper"
down_revision: Union[str, Sequence[str], None] = "0004_13f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _account_fk() -> sa.Column:
    return sa.Column("account_id", sa.Integer(), sa.ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("start_cash", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("fee_bps", sa.Float(), nullable=False),
        sa.Column("slippage_bps", sa.Float(), nullable=False),
        sa.Column("daily_loss_limit_pct", sa.Float(), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("halted_day", sa.Date(), nullable=True),
        sa.Column("day", sa.Date(), nullable=True),
        sa.Column("day_start_equity", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "paper_strategies",
        sa.Column("id", sa.Integer(), primary_key=True), _account_fk(),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("params", JSON, nullable=False),
        sa.Column("symbols", JSON, nullable=False),
        sa.Column("max_position_usd", sa.Float(), nullable=False),
        sa.Column("flatten_at_close", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_paper_strategies_account_id", "paper_strategies", ["account_id"])
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Integer(), primary_key=True), _account_fk(),
        sa.Column("strategy_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("avg_price", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_paper_positions_account_id", "paper_positions", ["account_id"])
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), primary_key=True), _account_fk(),
        sa.Column("strategy_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("fee", sa.Float(), nullable=False),
        sa.Column("realized_pl", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_paper_trades_account_id", "paper_trades", ["account_id"])
    op.create_index("ix_paper_trades_ts", "paper_trades", ["ts"])
    op.create_table(
        "paper_events",
        sa.Column("id", sa.Integer(), primary_key=True), _account_fk(),
        sa.Column("strategy_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(24), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("message", sa.String(400), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_paper_events_account_id", "paper_events", ["account_id"])
    op.create_index("ix_paper_events_ts", "paper_events", ["ts"])
    op.create_table(
        "paper_equity",
        sa.Column("id", sa.Integer(), primary_key=True), _account_fk(),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
    )
    op.create_index("ix_paper_equity_account_id", "paper_equity", ["account_id"])


def downgrade() -> None:
    for t in ("paper_equity", "paper_events", "paper_trades", "paper_positions", "paper_strategies", "paper_accounts"):
        op.drop_table(t)
