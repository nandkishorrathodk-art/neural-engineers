"""add durable fenced resource leases

Revision ID: 4d01836a2f91
Revises: 0b3a9cf12d54
Create Date: 2026-11-02 00:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4d01836a2f91"
down_revision: Union[str, None] = "0b3a9cf12d54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resource_leases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("resource_key", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("resource_reservation", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resource_key", "fencing_token", name="uq_resource_lease_fence"),
    )
    op.create_index("ix_resource_leases_resource_key", "resource_leases", ["resource_key"])
    op.create_index("ix_resource_leases_employee_id", "resource_leases", ["employee_id"])
    op.create_index("ix_resource_leases_task_id", "resource_leases", ["task_id"])
    op.create_index("ix_resource_leases_status", "resource_leases", ["status"])
    op.create_index("ix_resource_leases_lease_expires_at", "resource_leases", ["lease_expires_at"])
    op.create_index(
        "uq_resource_lease_active_resource",
        "resource_leases",
        ["resource_key"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_resource_lease_active_employee",
        "resource_leases",
        ["employee_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    retained_leases = connection.execute(sa.text("SELECT COUNT(*) FROM resource_leases")).scalar_one()
    if retained_leases:
        raise RuntimeError("Cannot discard durable resource lease history; export records before recovery downgrade.")
    op.drop_index("uq_resource_lease_active_employee", table_name="resource_leases")
    op.drop_index("uq_resource_lease_active_resource", table_name="resource_leases")
    op.drop_index("ix_resource_leases_lease_expires_at", table_name="resource_leases")
    op.drop_index("ix_resource_leases_status", table_name="resource_leases")
    op.drop_index("ix_resource_leases_task_id", table_name="resource_leases")
    op.drop_index("ix_resource_leases_employee_id", table_name="resource_leases")
    op.drop_index("ix_resource_leases_resource_key", table_name="resource_leases")
    op.drop_table("resource_leases")
