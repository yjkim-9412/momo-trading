"""add market scope runtime fields

Revision ID: 9f2b3c4d5e6f
Revises: 506d4ef486ca
Create Date: 2026-03-13 17:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f2b3c4d5e6f"
down_revision: Union[str, None] = "506d4ef486ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("daily_reports", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("market_scope", sa.String(length=10), nullable=False, server_default="KRX")
        )
        batch_op.create_index("ix_daily_reports_market_scope", ["market_scope"], unique=False)
        batch_op.drop_index("ix_daily_reports_report_date")
        batch_op.create_index("ix_daily_reports_report_date", ["report_date"], unique=False)
        batch_op.create_unique_constraint(
            "uq_daily_reports_market_scope_report_date",
            ["market_scope", "report_date"],
        )

    with op.batch_alter_table("trading_rules", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("market_scope", sa.String(length=10), nullable=False, server_default="KRX")
        )
        batch_op.create_index("ix_trading_rules_market_scope", ["market_scope"], unique=False)

    with op.batch_alter_table("agent_activity_logs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("market_scope", sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column("trading_date", sa.Date(), nullable=True))
        batch_op.create_index("ix_agent_activity_logs_market_scope", ["market_scope"], unique=False)
        batch_op.create_index("ix_agent_activity_logs_trading_date", ["trading_date"], unique=False)

    op.execute("UPDATE daily_reports SET market_scope = 'KRX' WHERE market_scope IS NULL")
    op.execute("UPDATE trading_rules SET market_scope = 'KRX' WHERE market_scope IS NULL")
    op.execute("UPDATE agent_activity_logs SET market_scope = 'KRX' WHERE market_scope IS NULL")
    op.execute(
        "UPDATE agent_activity_logs SET trading_date = DATE(created_at) "
        "WHERE trading_date IS NULL AND created_at IS NOT NULL"
    )

    with op.batch_alter_table("daily_reports", schema=None) as batch_op:
        batch_op.alter_column("market_scope", server_default=None)

    with op.batch_alter_table("trading_rules", schema=None) as batch_op:
        batch_op.alter_column("market_scope", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("agent_activity_logs", schema=None) as batch_op:
        batch_op.drop_index("ix_agent_activity_logs_trading_date")
        batch_op.drop_index("ix_agent_activity_logs_market_scope")
        batch_op.drop_column("trading_date")
        batch_op.drop_column("market_scope")

    with op.batch_alter_table("trading_rules", schema=None) as batch_op:
        batch_op.drop_index("ix_trading_rules_market_scope")
        batch_op.drop_column("market_scope")

    with op.batch_alter_table("daily_reports", schema=None) as batch_op:
        batch_op.drop_constraint("uq_daily_reports_market_scope_report_date", type_="unique")
        batch_op.drop_index("ix_daily_reports_market_scope")
        batch_op.drop_index("ix_daily_reports_report_date")
        batch_op.create_index("ix_daily_reports_report_date", ["report_date"], unique=True)
        batch_op.drop_column("market_scope")
