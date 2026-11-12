"""add execution capabilities to employees

Revision ID: a1b2c3d4e5f7
Revises: e5f6a7b8c9d0
Create Date: 2026-11-12 03:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("execution_capabilities", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employees", "execution_capabilities")
