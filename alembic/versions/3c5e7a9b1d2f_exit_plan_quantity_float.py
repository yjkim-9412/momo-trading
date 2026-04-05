"""exit_plan_quantity_float

Revision ID: 3c5e7a9b1d2f
Revises: e1f2a3b4c5d6
Create Date: 2026-04-02 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3c5e7a9b1d2f"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("exit_plans", schema=None) as batch_op:
        batch_op.alter_column(
            "total_quantity",
            existing_type=sa.Integer(),
            type_=sa.Float(),
            existing_nullable=False,
        )

    with op.batch_alter_table("exit_plan_history", schema=None) as batch_op:
        batch_op.alter_column(
            "total_quantity",
            existing_type=sa.Integer(),
            type_=sa.Float(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("exit_plan_history", schema=None) as batch_op:
        batch_op.alter_column(
            "total_quantity",
            existing_type=sa.Float(),
            type_=sa.Integer(),
            existing_nullable=False,
        )

    with op.batch_alter_table("exit_plans", schema=None) as batch_op:
        batch_op.alter_column(
            "total_quantity",
            existing_type=sa.Float(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
