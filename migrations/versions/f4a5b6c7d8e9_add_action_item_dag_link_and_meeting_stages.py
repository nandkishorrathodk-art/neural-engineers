"""add action item dag link and meeting stages

Revision ID: f4a5b6c7d8e9
Revises: 0d3b7e9a51c2
Create Date: 2026-11-11 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "0d3b7e9a51c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "action_items",
        sa.Column("linked_task_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "action_items",
        sa.Column("acceptance_criteria", sa.Text(), nullable=True),
    )
    op.create_index("ix_action_items_linked_task_id", "action_items", ["linked_task_id"], unique=False)
    op.add_column(
        "meetings",
        sa.Column("current_stage", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "current_stage")
    op.drop_index("ix_action_items_linked_task_id", table_name="action_items")
    op.drop_column("action_items", "acceptance_criteria")
    op.drop_column("action_items", "linked_task_id")

