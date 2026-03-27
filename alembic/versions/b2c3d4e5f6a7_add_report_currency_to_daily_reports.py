"""add report_currency to daily_reports

Revision ID: b2c3d4e5f6a7
Revises: a9b8c7d6e5f4
Create Date: 2026-03-27 16:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("daily_reports", schema=None) as batch_op:
        batch_op.add_column(sa.Column("report_currency", sa.String(length=10), nullable=False, server_default="KRW"))


def downgrade() -> None:
    with op.batch_alter_table("daily_reports", schema=None) as batch_op:
        batch_op.drop_column("report_currency")
