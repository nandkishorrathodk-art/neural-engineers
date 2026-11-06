"""merge service heartbeat and durable emergency-stop schema heads

Revision ID: b61a9d77c204
Revises: a7c9e2f1b4d0, f1b2c3d4e5f6
Create Date: 2026-11-06 12:00:00.000000
"""

from typing import Sequence, Union

revision: str = "b61a9d77c204"
down_revision: Union[str, tuple[str, str], None] = ("a7c9e2f1b4d0", "f1b2c3d4e5f6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
