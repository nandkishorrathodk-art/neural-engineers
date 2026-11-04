"""add authenticated worker and verifier runtime heartbeats

Revision ID: f1b2c3d4e5f6
Revises: d4f1ac7820be
Create Date: 2026-11-04 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1b2c3d4e5f6"
down_revision: Union[str, None] = "d4f1ac7820be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_heartbeats",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("service_type", sa.String(length=32), nullable=False),
        sa.Column("service_id", sa.String(length=128), nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_type", "service_id", name="uq_service_heartbeat_identity"
        ),
    )
    op.create_index(
        "ix_service_heartbeat_last_seen",
        "service_heartbeats",
        ["last_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_service_heartbeat_last_seen", table_name="service_heartbeats")
    op.drop_table("service_heartbeats")
