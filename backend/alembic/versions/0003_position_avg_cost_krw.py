"""portfolio_positions.avg_cost_krw

Revision ID: 0003_avg_cost_krw
Revises: 0002_avg_cost_estimated
Create Date: 2026-10-10

KRW average cost for foreign holdings, so P/L can be split into price and FX (환차손익).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_avg_cost_krw"
down_revision: Union[str, Sequence[str], None] = "0002_avg_cost_estimated"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("portfolio_positions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("avg_cost_krw", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("portfolio_positions", schema=None) as batch_op:
        batch_op.drop_column("avg_cost_krw")
