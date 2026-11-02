"""persist declared execution mode on tasks

Revision ID: 93c827d011ac
Revises: 0b3a9cf12d54
Create Date: 2026-11-02 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "93c827d011ac"
down_revision: Union[str, None] = "0b3a9cf12d54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(
            sa.Column("execution_mode", sa.String(length=32), server_default="terminal", nullable=False)
        )
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column("execution_mode", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("execution_mode")
