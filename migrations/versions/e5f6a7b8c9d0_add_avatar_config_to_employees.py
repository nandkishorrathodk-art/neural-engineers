"""add avatar config to employees

Revision ID: e5f6a7b8c9d0
Revises: f4a5b6c7d8e9
Create Date: 2026-11-11 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("avatar_config", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employees", "avatar_config")
