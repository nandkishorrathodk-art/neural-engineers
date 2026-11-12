import asyncio
from decimal import Decimal

import pytest
from neural.db.engine import Base
from neural.db.models import BudgetAccount, BudgetReservation
from neural.db.repositories import BudgetRepository
from neural.exceptions import QuotaExceededError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_reservations_are_idempotent_and_atomic_across_scopes(async_session):
    repo = BudgetRepository(async_session)
    scopes = {"month:2026-11": Decimal("1.00"), "project:p1": Decimal("0.50")}

    first = await repo.reserve(
        idempotency_key="call-1",
        reserved_usd=Decimal("0.40"),
        scope_caps=scopes,
    )
    duplicate = await repo.reserve(
        idempotency_key="call-1",
        reserved_usd=Decimal("0.40"),
        scope_caps=scopes,
    )
    assert duplicate.id == first.id

    with pytest.raises(QuotaExceededError):
        await repo.reserve(
            idempotency_key="call-2",
            reserved_usd=Decimal("0.20"),
            scope_caps=scopes,
        )

    month = await async_session.get(BudgetAccount, "month:2026-11")
    project = await async_session.get(BudgetAccount, "project:p1")
    assert month is not None and month.reserved_usd == Decimal("0.40000000")
    assert project is not None and project.reserved_usd == Decimal("0.40000000")
    assert await async_session.scalar(
        select(BudgetReservation.id).where(BudgetReservation.idempotency_key == "call-2")
    ) is None


@pytest.mark.asyncio
async def test_settlement_moves_reserved_to_spent_and_records_overrun(async_session):
    repo = BudgetRepository(async_session)
    reservation = await repo.reserve(
        idempotency_key="call-settle",
        reserved_usd=Decimal("0.40"),
        scope_caps={"month:2026-11": Decimal("1.00"), "project:p2": Decimal("1.00")},
    )

    settled = await repo.settle(
        reservation.id,
        actual_usd=Decimal("0.30"),
        usage_receipt={"provider": "test-provider", "input_tokens": 10, "output_tokens": 20},
    )
    assert settled.status == "settled"
    assert settled.actual_usd == Decimal("0.30000000")
    assert settled.usage_receipt["provider"] == "test-provider"
    month = await async_session.get(BudgetAccount, "month:2026-11")
    assert month is not None
    assert month.reserved_usd == Decimal("0E-8")
    assert month.spent_usd == Decimal("0.30000000")

    overrun = await repo.reserve(
        idempotency_key="call-overrun",
        reserved_usd=Decimal("0.10"),
        scope_caps={"month:2026-11": Decimal("1.00"), "project:p2": Decimal("1.00")},
    )
    breached = await repo.settle(
        overrun.id,
        actual_usd=Decimal("0.12"),
        usage_receipt={"provider": "test-provider", "reported_over_reservation": True},
    )
    assert breached.status == "overrun"
    assert month.spent_usd == Decimal("0.42000000")


@pytest.mark.asyncio
async def test_ambiguous_outcome_cannot_release_reserved_budget(async_session):
    repo = BudgetRepository(async_session)
    reservation = await repo.reserve(
        idempotency_key="call-release",
        reserved_usd=Decimal("0.25"),
        scope_caps={"month:2026-11": Decimal("1.00")},
    )

    with pytest.raises(ValueError, match="ambiguous provider outcomes"):
        await repo.release_after_confirmed_no_charge(
            reservation.id,
            provider_confirmed_unbilled=False,
            reason="Provider timed out without a receipt",
        )

    released = await repo.release_after_confirmed_no_charge(
        reservation.id,
        provider_confirmed_unbilled=True,
        reason="Provider returned a signed no-charge receipt",
    )
    assert released.status == "released"
    assert released.release_reason == "Provider returned a signed no-charge receipt"
    month = await async_session.get(BudgetAccount, "month:2026-11")
    assert month is not None and month.reserved_usd == Decimal("0E-8")


@pytest.mark.asyncio
async def test_reserve_settle_release_survives_sqlite_float_drift(async_session):
    """Many reserve/settle cycles must not strand a reservation.

    SQLite stores NUMERIC as REAL, so repeated add/subtract leaves the account
    a hair below the preflight amount (e.g. 0.9999999999999997). Releasing the
    preflight must still succeed and clamp the account back to zero.
    """
    repo = BudgetRepository(async_session)
    scopes = {"month:2026-11": 500.0}
    preflight = await repo.reserve(
        idempotency_key="preflight",
        reserved_usd=1.0,
        scope_caps=scopes,
    )
    drifting_amounts = [0.0128586, 0.010206, 0.000476, 0.01129725, 0.00621053]
    for index, amount in enumerate(drifting_amounts):
        reservation = await repo.reserve(
            idempotency_key=f"llm-{index}",
            reserved_usd=amount,
            scope_caps=scopes,
        )
        await repo.settle(reservation.id, actual_usd=0.0005, usage_receipt={"ok": True})

    released = await repo.release_after_confirmed_no_charge(
        preflight.id,
        provider_confirmed_unbilled=True,
        reason="Planning completed without provider charge.",
    )

    assert released.status == "released"
    month = await async_session.get(BudgetAccount, "month:2026-11")
    assert month is not None
    assert month.reserved_usd == Decimal("0E-8")


@pytest.mark.asyncio
async def test_invalid_reservations_are_rejected(async_session):
    repo = BudgetRepository(async_session)
    for amount in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            await repo.reserve(
                idempotency_key=f"invalid-{amount}",
                reserved_usd=amount,
                scope_caps={"month:2026-11": 1},
            )


@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_oversubscribe_a_shared_account(tmp_path):
    database_path = tmp_path / "concurrent-budget.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def attempt_reservation(key: str):
        async with session_factory() as session:
            return await BudgetRepository(session).reserve(
                idempotency_key=key,
                reserved_usd=Decimal("0.08"),
                scope_caps={"month:2026-11": Decimal("0.16")},
            )

    duplicates = await asyncio.gather(
        attempt_reservation("same-concurrent-call"),
        attempt_reservation("same-concurrent-call"),
        return_exceptions=True,
    )
    assert all(not isinstance(result, Exception) for result in duplicates)
    assert duplicates[0].id == duplicates[1].id

    results = await asyncio.gather(
        attempt_reservation("concurrent-a"),
        attempt_reservation("concurrent-b"),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, QuotaExceededError) for result in results) == 1

    async with session_factory() as session:
        account = await session.get(BudgetAccount, "month:2026-11")
        assert account is not None
        assert account.reserved_usd == Decimal("0.16000000")
        assert account.reserved_usd + account.spent_usd <= account.cap_usd
    await engine.dispose()
