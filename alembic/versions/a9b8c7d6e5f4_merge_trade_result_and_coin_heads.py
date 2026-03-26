"""merge trade result and coin heads

Revision ID: a9b8c7d6e5f4
Revises: d4e5f6a7b8c9, f1a2b3c4d5e6
Create Date: 2026-03-26 14:45:00.000000

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, tuple[str, str], None] = ("d4e5f6a7b8c9", "f1a2b3c4d5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Merge-only revision."""


def downgrade() -> None:
    """Merge-only revision."""
