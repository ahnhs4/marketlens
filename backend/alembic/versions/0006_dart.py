"""OpenDART corp codes and financial statements

Revision ID: 0006_dart
Revises: 0005_paper
Create Date: 2026-11-05

Korean financial statements (main accounts for the whole market, full statements per company, dividends)
for the KR fundamental score and the value-up / low-PBR page.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_dart"
down_revision: Union[str, Sequence[str], None] = "0005_paper"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "dart_corps",
        sa.Column("corp_code", sa.String(8), primary_key=True),
        sa.Column("corp_name", sa.String(200), nullable=False),
        sa.Column("stock_code", sa.String(12), nullable=False),
        sa.Column("modify_date", sa.String(8), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dart_corps_stock_code", "dart_corps", ["stock_code"])
    op.create_table(
        "dart_financials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("corp_code", sa.String(8), nullable=False),
        sa.Column("stock_code", sa.String(12), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("reprt", sa.String(5), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("fs_div", sa.String(3), nullable=True),
        sa.Column("rcept_no", sa.String(20), nullable=True),
        sa.Column("data", JSON, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("corp_code", "year", "reprt", "kind", name="uq_dart_fin_period"),
    )
    op.create_index("ix_dart_financials_corp_code", "dart_financials", ["corp_code"])
    op.create_index("ix_dart_financials_stock_code", "dart_financials", ["stock_code"])


def downgrade() -> None:
    op.drop_index("ix_dart_financials_stock_code", table_name="dart_financials")
    op.drop_index("ix_dart_financials_corp_code", table_name="dart_financials")
    op.drop_table("dart_financials")
    op.drop_index("ix_dart_corps_stock_code", table_name="dart_corps")
    op.drop_table("dart_corps")
