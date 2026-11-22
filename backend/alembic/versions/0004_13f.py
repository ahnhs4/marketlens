"""13F filings, holdings and the CUSIP -> ticker map

Revision ID: 0004_13f
Revises: 0003_avg_cost_krw
Create Date: 2026-10-10

Quarterly 13F-HR positions of large hedge funds and banks (SEC EDGAR), for the institutions page.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_13f"
down_revision: Union[str, Sequence[str], None] = "0003_avg_cost_krw"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "filings_13f",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("manager", sa.String(200), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("filed", sa.Date(), nullable=False),
        sa.Column("accession", sa.String(30), nullable=False, unique=True),
        sa.Column("equity_value", sa.Float(), nullable=False),
        sa.Column("positions", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cik", "period", name="uq_13f_cik_period"),
    )
    op.create_index("ix_filings_13f_cik", "filings_13f", ["cik"])
    op.create_table(
        "holdings_13f",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filing_id", sa.Integer(), sa.ForeignKey("filings_13f.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cusip", sa.String(9), nullable=False),
        sa.Column("issuer", sa.String(200), nullable=False),
        sa.Column("title", sa.String(100), nullable=True),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("shares", sa.Float(), nullable=False),
    )
    op.create_index("ix_holdings_13f_filing_id", "holdings_13f", ["filing_id"])
    op.create_index("ix_holdings_13f_cusip", "holdings_13f", ["cusip"])
    op.create_table(
        "cusip_map",
        sa.Column("cusip", sa.String(9), primary_key=True),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("security_type", sa.String(60), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cusip_map")
    op.drop_index("ix_holdings_13f_cusip", table_name="holdings_13f")
    op.drop_index("ix_holdings_13f_filing_id", table_name="holdings_13f")
    op.drop_table("holdings_13f")
    op.drop_index("ix_filings_13f_cik", table_name="filings_13f")
    op.drop_table("filings_13f")
