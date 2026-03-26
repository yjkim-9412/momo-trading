"""add trade_result take profit price

Revision ID: f1a2b3c4d5e6
Revises: c4d7e8f9a1b2
Create Date: 2026-03-26 14:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "c4d7e8f9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("trade_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ai_take_profit_price", sa.Float(), nullable=True))

    op.execute(
        """
        UPDATE trade_results
        SET ai_take_profit_price = ai_target_price
        WHERE ai_take_profit_price IS NULL
          AND COALESCE(ai_target_price, 0) > 0
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("trade_results", schema=None) as batch_op:
        batch_op.drop_column("ai_take_profit_price")
