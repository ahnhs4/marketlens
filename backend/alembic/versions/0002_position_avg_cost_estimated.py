"""portfolio_positions.avg_cost_estimated

Revision ID: 0002_avg_cost_estimated
Revises: 0001_initial
Create Date: 2026-10-09

Marks positions whose average cost is a stand-in (e.g. imported from a balance
certificate without purchase prices) so P/L is not reported as a return.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_avg_cost_estimated"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("portfolio_positions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("avg_cost_estimated", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("portfolio_positions", schema=None) as batch_op:
        batch_op.drop_column("avg_cost_estimated")
