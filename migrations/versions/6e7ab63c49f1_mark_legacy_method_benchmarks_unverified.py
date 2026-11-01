"""mark heuristic method benchmark history unverified

Revision ID: 6e7ab63c49f1
Revises: 4d01836a2f91
Create Date: 2026-11-02 01:05:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6e7ab63c49f1"
down_revision: Union[str, None] = "4d01836a2f91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("method_benchmarks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "evaluation_status",
                sa.String(length=32),
                server_default=sa.text("'legacy_structural_heuristic'"),
                nullable=False,
            )
        )
    op.execute(
        sa.text("UPDATE method_benchmarks SET recommendation = 'legacy_unverified'")
    )
    with op.batch_alter_table("method_benchmarks") as batch_op:
        batch_op.alter_column(
            "evaluation_status",
            existing_type=sa.String(length=32),
            server_default=sa.text("'not_evaluated'"),
            existing_nullable=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    unverified = connection.execute(
        sa.text("SELECT COUNT(*) FROM method_benchmarks WHERE evaluation_status != 'not_evaluated'")
    ).scalar_one()
    if unverified:
        raise RuntimeError("Cannot erase benchmark provenance labels from retained method benchmark history.")
    with op.batch_alter_table("method_benchmarks") as batch_op:
        batch_op.drop_column("evaluation_status")
