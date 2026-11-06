"""add transactional budget reservation ledger

Revision ID: c7d3e1b9a620
Revises: b61a9d77c204
Create Date: 2026-11-06 12:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7d3e1b9a620"
down_revision: Union[str, None] = "b61a9d77c204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "budget_accounts",
        sa.Column("scope_key", sa.String(length=192), nullable=False),
        sa.Column("cap_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("spent_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("cap_usd >= 0", name="ck_budget_account_cap_nonnegative"),
        sa.CheckConstraint("reserved_usd >= 0", name="ck_budget_account_reserved_nonnegative"),
        sa.CheckConstraint("spent_usd >= 0", name="ck_budget_account_spent_nonnegative"),
        sa.PrimaryKeyConstraint("scope_key"),
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("actual_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("scope_keys", sa.JSON(), nullable=False),
        sa.Column("scope_caps", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("usage_receipt", sa.JSON(), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("reserved_usd > 0", name="ck_budget_reservation_amount_positive"),
        sa.CheckConstraint(
            "actual_usd IS NULL OR actual_usd >= 0",
            name="ck_budget_reservation_actual_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_budget_reservation_idempotency"),
    )
    op.create_index(
        "ix_budget_reservation_status_created",
        "budget_reservations",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_budget_reservation_status_created", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_table("budget_accounts")
