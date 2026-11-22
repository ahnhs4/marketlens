"""Strategy builder and trade journal

Revision ID: 0007_lab
Revises: 0006_dart
Create Date: 2026-11-15

custom_strategies: user-built entry/exit rule sets. trade_journal: hand-written record of trades actually made.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_lab"
down_revision: Union[str, Sequence[str], None] = "0006_dart"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "custom_strategies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("spec", JSON, nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_custom_strategy_user_name"),
    )
    op.create_index("ix_custom_strategies_user_id", "custom_strategies", ["user_id"])
    op.create_table(
        "trade_journal",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(24), nullable=False),
        sa.Column("name", sa.String(120)),
        sa.Column("currency", sa.String(8)),
        sa.Column("strategy", sa.String(80), nullable=False, server_default=""),
        sa.Column("signal", sa.String(300)),
        sa.Column("followed", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("entry_date", sa.Date, nullable=False),
        sa.Column("entry_price", sa.Float, nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("stop_price", sa.Float),
        sa.Column("target_price", sa.Float),
        sa.Column("exit_date", sa.Date),
        sa.Column("exit_price", sa.Float),
        sa.Column("fees", sa.Float, nullable=False, server_default="0"),
        sa.Column("note", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trade_journal_user_id", "trade_journal", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_trade_journal_user_id", table_name="trade_journal")
    op.drop_table("trade_journal")
    op.drop_index("ix_custom_strategies_user_id", table_name="custom_strategies")
    op.drop_table("custom_strategies")
