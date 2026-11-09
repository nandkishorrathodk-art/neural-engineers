"""add scheduled task execution time

Revision ID: 0d3b7e9a51c2
Revises: c7d3e1b9a620
Create Date: 2026-11-08 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0d3b7e9a51c2"
down_revision: Union[str, None] = "c7d3e1b9a620"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("scheduled_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tasks_scheduled_at", "tasks", ["scheduled_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_scheduled_at", table_name="tasks")
    op.drop_column("tasks", "scheduled_at")
