"""persist a task-attempt workspace baseline for verifier deltas

Revision ID: d2a7f91b6c04
Revises: f1b2c3d4e5f6
Create Date: 2026-11-04 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2a7f91b6c04"
down_revision: Union[str, None] = "f1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_attempts",
        sa.Column("workspace_manifest_before", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    connection = op.get_bind()
    baselines = connection.execute(
        sa.text("SELECT COUNT(*) FROM task_attempts WHERE workspace_manifest_before IS NOT NULL")
    ).scalar_one()
    if baselines:
        raise RuntimeError("Cannot remove task-start workspace evidence after it has been captured.")
    op.drop_column("task_attempts", "workspace_manifest_before")
