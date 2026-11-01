"""add one-time approval consumption receipts

Revision ID: a821c490b55a
Revises: 6e7ab63c49f1
Create Date: 2026-11-02 10:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a821c490b55a"
down_revision: Union[str, None] = "6e7ab63c49f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(sa.Column("consumed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("consumed_by", sa.String(length=128), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    consumed = connection.execute(
        sa.text("SELECT COUNT(*) FROM approvals WHERE consumed_at IS NOT NULL")
    ).scalar_one()
    if consumed:
        raise RuntimeError("Cannot remove the durable record of consumed approvals.")
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_column("consumed_by")
        batch_op.drop_column("consumed_at")
