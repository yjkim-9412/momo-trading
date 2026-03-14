"""add_coin_buy_amount_fields

Revision ID: b7c8d9e0f1a2
Revises: 7992d9813738
Create Date: 2026-03-14 23:58:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "7992d9813738"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("coin_recommendations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "suggested_amount_krw",
                sa.Float(),
                nullable=False,
                server_default="0",
            )
        )

    op.execute(
        """
        UPDATE coin_recommendations
        SET suggested_amount_krw = CASE
            WHEN COALESCE(suggested_amount_krw, 0) > 0 THEN suggested_amount_krw
            ELSE COALESCE(suggested_price, 0) * COALESCE(suggested_quantity, 0)
        END
        """
    )

    with op.batch_alter_table("coin_broker_orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "order_type",
                sa.String(length=10),
                nullable=False,
                server_default="LIMIT",
            )
        )
        batch_op.add_column(
            sa.Column(
                "requested_amount_krw",
                sa.Float(),
                nullable=False,
                server_default="0",
            )
        )

    op.execute(
        """
        UPDATE coin_broker_orders
        SET requested_amount_krw = CASE
            WHEN COALESCE(requested_amount_krw, 0) > 0 THEN requested_amount_krw
            WHEN UPPER(COALESCE(side, '')) = 'BUY' THEN COALESCE(requested_price, 0) * COALESCE(quantity, 0)
            ELSE 0
        END
        """
    )
    op.execute(
        """
        UPDATE coin_broker_orders
        SET order_type = CASE
            WHEN COALESCE(order_type, '') <> '' THEN order_type
            WHEN COALESCE(requested_price, 0) > 0 THEN 'LIMIT'
            WHEN UPPER(COALESCE(side, '')) = 'BUY' AND COALESCE(requested_amount_krw, 0) > 0 THEN 'PRICE'
            ELSE 'MARKET'
        END
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("coin_broker_orders", schema=None) as batch_op:
        batch_op.drop_column("requested_amount_krw")
        batch_op.drop_column("order_type")

    with op.batch_alter_table("coin_recommendations", schema=None) as batch_op:
        batch_op.drop_column("suggested_amount_krw")
