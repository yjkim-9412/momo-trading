"""add_exit_plan_tables

Revision ID: e1f2a3b4c5d6
Revises: b2c3d4e5f6a7
Create Date: 2026-04-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exit_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("market", sa.String(length=10), nullable=False),
        sa.Column("avg_entry_price", sa.Float(), nullable=False),
        sa.Column("total_quantity", sa.Integer(), nullable=False),
        sa.Column("levels", sa.Text(), nullable=False),
        sa.Column("trailing_stop_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("highest_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("exit_plans", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_exit_plans_symbol"), ["symbol"], unique=False)
        batch_op.create_index(batch_op.f("ix_exit_plans_is_active"), ["is_active"], unique=False)

    op.create_table(
        "exit_plan_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("exit_plan_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_levels", sa.Text(), nullable=True),
        sa.Column("new_levels", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=False),
        sa.Column("avg_entry_price", sa.Float(), nullable=False),
        sa.Column("total_quantity", sa.Integer(), nullable=False),
        sa.Column("ai_reasoning", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["exit_plan_id"], ["exit_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("exit_plan_history", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_exit_plan_history_exit_plan_id"), ["exit_plan_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("exit_plan_history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_exit_plan_history_exit_plan_id"))
    op.drop_table("exit_plan_history")

    with op.batch_alter_table("exit_plans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_exit_plans_is_active"))
        batch_op.drop_index(batch_op.f("ix_exit_plans_symbol"))
    op.drop_table("exit_plans")
