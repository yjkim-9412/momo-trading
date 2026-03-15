"""add broker_orders table and trade_result fx fields

Revision ID: c4d7e8f9a1b2
Revises: 9f2b3c4d5e6f
Create Date: 2026-03-13 23:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d7e8f9a1b2"
down_revision: Union[str, None] = "9f2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broker_orders",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("cycle_id", sa.String(length=36), nullable=True),
        sa.Column("market", sa.String(length=10), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("stock_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="SUBMITTED"),
        sa.Column("strategy_type", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("requested_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("requested_price_krw", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("filled_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("filled_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("filled_price_krw", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="KRW"),
        sa.Column("exchange_rate_to_krw", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("kis_order_id", sa.String(length=50), nullable=False),
        sa.Column("status_detail", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("filled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("broker_orders", schema=None) as batch_op:
        batch_op.create_index("ix_broker_orders_cycle_id", ["cycle_id"], unique=False)
        batch_op.create_index("ix_broker_orders_market", ["market"], unique=False)
        batch_op.create_index("ix_broker_orders_symbol", ["symbol"], unique=False)
        batch_op.create_index("ix_broker_orders_status", ["status"], unique=False)
        batch_op.create_index("ix_broker_orders_kis_order_id", ["kis_order_id"], unique=False)

    with op.batch_alter_table("trade_results", schema=None) as batch_op:
        batch_op.add_column(sa.Column("currency", sa.String(length=10), nullable=False, server_default="KRW"))
        batch_op.add_column(sa.Column("exchange_rate_to_krw", sa.Float(), nullable=False, server_default="1.0"))
        batch_op.add_column(sa.Column("entry_price_krw", sa.Float(), nullable=False, server_default="0.0"))
        batch_op.add_column(sa.Column("exit_price_krw", sa.Float(), nullable=False, server_default="0.0"))
        batch_op.add_column(sa.Column("raw_pnl", sa.Float(), nullable=False, server_default="0.0"))

    with op.batch_alter_table("broker_orders", schema=None) as batch_op:
        batch_op.alter_column("stock_name", server_default=None)
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column("strategy_type", server_default=None)
        batch_op.alter_column("requested_price", server_default=None)
        batch_op.alter_column("requested_price_krw", server_default=None)
        batch_op.alter_column("filled_quantity", server_default=None)
        batch_op.alter_column("filled_price", server_default=None)
        batch_op.alter_column("filled_price_krw", server_default=None)
        batch_op.alter_column("currency", server_default=None)
        batch_op.alter_column("exchange_rate_to_krw", server_default=None)

    with op.batch_alter_table("trade_results", schema=None) as batch_op:
        batch_op.alter_column("currency", server_default=None)
        batch_op.alter_column("exchange_rate_to_krw", server_default=None)
        batch_op.alter_column("entry_price_krw", server_default=None)
        batch_op.alter_column("exit_price_krw", server_default=None)
        batch_op.alter_column("raw_pnl", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("trade_results", schema=None) as batch_op:
        batch_op.drop_column("raw_pnl")
        batch_op.drop_column("exit_price_krw")
        batch_op.drop_column("entry_price_krw")
        batch_op.drop_column("exchange_rate_to_krw")
        batch_op.drop_column("currency")

    with op.batch_alter_table("broker_orders", schema=None) as batch_op:
        batch_op.drop_index("ix_broker_orders_kis_order_id")
        batch_op.drop_index("ix_broker_orders_status")
        batch_op.drop_index("ix_broker_orders_symbol")
        batch_op.drop_index("ix_broker_orders_market")
        batch_op.drop_index("ix_broker_orders_cycle_id")

    op.drop_table("broker_orders")
