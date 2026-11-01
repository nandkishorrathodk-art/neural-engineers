"""add independent verification receipts to task attempts

Revision ID: 0b3a9cf12d54
Revises: 5f26f32ea7d6
Create Date: 2026-11-01 23:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0b3a9cf12d54"
down_revision: Union[str, None] = "5f26f32ea7d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("task_attempts") as batch_op:
        batch_op.add_column(sa.Column("verification_receipt", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("verified_by", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    receipts = connection.execute(
        sa.text("SELECT COUNT(*) FROM task_attempts WHERE verification_receipt IS NOT NULL")
    ).scalar_one()
    if receipts:
        raise RuntimeError("Cannot discard persisted verification receipts from task attempt history.")
    with op.batch_alter_table("task_attempts") as batch_op:
        batch_op.drop_column("verified_at")
        batch_op.drop_column("verified_by")
        batch_op.drop_column("verification_receipt")
