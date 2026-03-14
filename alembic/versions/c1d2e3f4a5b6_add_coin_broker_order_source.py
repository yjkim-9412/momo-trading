"""add_coin_broker_order_source

Revision ID: c1d2e3f4a5b6
Revises: b7c8d9e0f1a2
Create Date: 2026-03-15 01:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("coin_broker_orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=20),
                nullable=False,
                server_default="AI",
            )
        )

    op.execute(
        """
        UPDATE coin_broker_orders
        SET source = CASE
            WHEN COALESCE(source, '') <> '' THEN source
            ELSE 'AI'
        END
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("coin_broker_orders", schema=None) as batch_op:
        batch_op.drop_column("source")
