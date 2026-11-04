"""persist workstation desk assignments and boardroom active speakers

Revision ID: e3f4a5b6c7d8
Revises: d2a7f91b6c04
Create Date: 2026-11-04 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2a7f91b6c04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("desk_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_employees_desk_id", "employees", ["desk_id"], unique=False)
    op.add_column(
        "meetings",
        sa.Column("active_speaker_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meetings", "active_speaker_id")
    op.drop_index("ix_employees_desk_id", table_name="employees")
    op.drop_column("employees", "desk_id")
