"""add durable shared emergency stop state

Revision ID: a7c9e2f1b4d0
Revises: e3f4a5b6c7d8
Create Date: 2026-11-04 13:00:00.000000
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e2f1b4d0"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "emergency_stop_state",
        sa.Column("state_key", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(length=128), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("state_key"),
    )
    op.bulk_insert(
        sa.table(
            "emergency_stop_state",
            sa.column("state_key", sa.String(length=64)),
            sa.column("active", sa.Boolean()),
            sa.column("changed_at", sa.DateTime()),
        ),
        [{"state_key": "global", "active": False, "changed_at": datetime.now(timezone.utc).replace(tzinfo=None)}],
    )


def downgrade() -> None:
    # Dropping a recorded emergency halt could silently resume production after
    # downgrade, so refuse to discard an active state.
    bind = op.get_bind()
    active = bind.execute(
        sa.text("SELECT active FROM emergency_stop_state WHERE state_key = 'global'")
    ).scalar()
    if active:
        raise RuntimeError("Cannot downgrade while the global emergency stop is active.")
    op.drop_table("emergency_stop_state")
