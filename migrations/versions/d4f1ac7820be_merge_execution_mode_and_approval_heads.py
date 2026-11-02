"""merge execution mode and approval migration heads

Revision ID: d4f1ac7820be
Revises: 93c827d011ac, a821c490b55a
Create Date: 2026-11-02 13:00:00.000000
"""

from typing import Sequence, Union

revision: str = "d4f1ac7820be"
down_revision: Union[str, tuple[str, str], None] = ("93c827d011ac", "a821c490b55a")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
