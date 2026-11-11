"""
Adversarial Empirical Stress Tests for Milestone 1 (R1):
Budget Enforcement, Quota Management, Reservation Lifecycle, and Settlement Accounting.
Authored by Challenger 2 (Replacement).
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from neural.boss.brain import BossBrain
from neural.boss.planner import TaskPlanner
from neural.db.models import BudgetAccount, BudgetReservation
from neural.db.repositories import BudgetRepository, ProjectRepository, TaskRepository
from neural.exceptions import ClarificationRequiredError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def current_month_scope() -> str:
    return f"organization-month:{datetime.now(timezone.utc):%Y-%m}"


@pytest.mark.asyncio
async def test_unbilled_release_provider_confirmed_unbilled_false_rejected_and_preserved(
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Challenge 1.1:
    Verify that if provider_confirmed_unbilled=False is passed to release_after_confirmed_no_charge:
    1. It raises ValueError with message 'ambiguous provider outcomes must remain reserved'.
    2. The reservation status remains 'reserved' (NOT released).
    3. The budget account's reserved_usd is preserved (not deducted).
    4. Subsequent release with provider_confirmed_unbilled=True succeeds cleanly.
    """
    repo = BudgetRepository(async_session)
    scopes = {current_month_scope: Decimal("1000.00")}

    reservation = await repo.reserve(
        idempotency_key="challenger-unbilled-false-test",
        reserved_usd=Decimal("25.00"),
        scope_caps=scopes,
    )
    assert reservation.status == "reserved"

    # Verify initial account state
    account_initial = await async_session.get(BudgetAccount, current_month_scope)
    assert account_initial is not None
    assert account_initial.reserved_usd == Decimal("25.00000000")
    assert account_initial.spent_usd == Decimal("0E-8")

    # 1. False should raise ValueError
    with pytest.raises(ValueError, match="ambiguous provider outcomes must remain reserved"):
        await repo.release_after_confirmed_no_charge(
            reservation.id,
            provider_confirmed_unbilled=False,
            reason="Network timeout without confirmed receipt",
        )

    # 2. Falsy non-boolean values should also raise ValueError
    for invalid_flag in (None, 0, "", "false"):
        with pytest.raises(ValueError, match="ambiguous provider outcomes must remain reserved"):
            await repo.release_after_confirmed_no_charge(
                reservation.id,
                provider_confirmed_unbilled=invalid_flag,  # type: ignore
                reason="Invalid flag test",
            )

    # 3. Check DB integrity: reservation status MUST still be 'reserved'
    res_check = await async_session.get(BudgetReservation, reservation.id)
    assert res_check is not None
    assert res_check.status == "reserved", "Status must remain 'reserved' after rejected release"

    # 4. Check account quota: reserved_usd MUST still be 25.00
    account_check = await async_session.get(BudgetAccount, current_month_scope)
    assert account_check is not None
    assert account_check.reserved_usd == Decimal("25.00000000"), "Reserved quota must be preserved"
    assert account_check.spent_usd == Decimal("0E-8")

    # 5. Subsequent valid release with provider_confirmed_unbilled=True should cleanly release
    released = await repo.release_after_confirmed_no_charge(
        reservation.id,
        provider_confirmed_unbilled=True,
        reason="Subsequent provider check confirmed zero tokens were billed",
    )
    assert released.status == "released"
    account_final = await async_session.get(BudgetAccount, current_month_scope)
    assert account_final is not None
    assert account_final.reserved_usd == Decimal("0E-8")
    assert account_final.spent_usd == Decimal("0E-8")


@pytest.mark.asyncio
async def test_planning_crashes_and_timeouts_cleanly_release_quota(
    async_session: AsyncSession,
    current_month_scope: str,
    monkeypatch,
):
    """
    Challenge 1.2:
    Verify that when model planning encounters a crash (RuntimeError), a timeout (TimeoutError),
    or clarification requirement (ClarificationRequiredError):
    1. The exception is propagated to the caller.
    2. The pre-reserved quota is cleanly released via release_after_confirmed_no_charge.
    3. No orphaned reservations or quota locks remain in the database.
    """
    # Seed month account
    account = BudgetAccount(
        scope_key=current_month_scope,
        cap_usd=Decimal("500.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    p_repo = ProjectRepository(async_session)
    t_repo = TaskRepository(async_session)
    brain = BossBrain(project_repo=p_repo, task_repo=t_repo)

    # Scenario A: TimeoutError during planning
    async def timeout_decompose(*args, **kwargs):
        raise TimeoutError("Model provider request timed out after 30 seconds")

    monkeypatch.setattr(TaskPlanner, "decompose_objective", timeout_decompose)

    with pytest.raises(TimeoutError, match="Model provider request timed out"):
        await brain.plan_and_create_project(
            objective="Build real-time streaming pipeline",
            project_budget_usd=100.0,
        )

    # Verify reservation released and headroom fully restored
    res_timeout = (
        await async_session.scalars(
            select(BudgetReservation).where(BudgetReservation.status == "released")
        )
    ).first()
    assert res_timeout is not None
    assert "timed out" in (res_timeout.release_reason or "").lower()

    refreshed_month = await async_session.get(BudgetAccount, current_month_scope)
    assert refreshed_month is not None
    assert refreshed_month.reserved_usd == Decimal("0E-8"), "Quota must not remain locked on timeout"
    assert refreshed_month.spent_usd == Decimal("0E-8")

    # Scenario B: RuntimeError / Model Worker Crash during planning
    async def crash_decompose(*args, **kwargs):
        raise RuntimeError("Worker process terminated abruptly: SIGSEGV (core dumped)")

    monkeypatch.setattr(TaskPlanner, "decompose_objective", crash_decompose)

    with pytest.raises(RuntimeError, match="Worker process terminated abruptly"):
        await brain.plan_and_create_project(
            objective="Train neural routing model",
            project_budget_usd=50.0,
        )

    # Verify all reservations are released, none stuck in 'reserved'
    active_reservations = (
        await async_session.scalars(
            select(BudgetReservation).where(BudgetReservation.status == "reserved")
        )
    ).all()
    assert len(active_reservations) == 0, f"Found {len(active_reservations)} orphaned reservations!"

    refreshed_month_2 = await async_session.get(BudgetAccount, current_month_scope)
    assert refreshed_month_2 is not None
    assert refreshed_month_2.reserved_usd == Decimal("0E-8"), "Quota must not remain locked on crash"
    assert refreshed_month_2.spent_usd == Decimal("0E-8")

    # Scenario C: Clarification required aborts planning
    async def clarification_decompose(*args, **kwargs):
        return {
            "clarification_required": True,
            "clarifying_questions": ["What database engine?", "What target SLA?"],
        }

    monkeypatch.setattr(TaskPlanner, "decompose_objective", clarification_decompose)

    with pytest.raises(ClarificationRequiredError):
        await brain.plan_and_create_project(
            objective="Unclear objective needing questions",
            project_budget_usd=30.0,
        )

    active_reservations_3 = (
        await async_session.scalars(
            select(BudgetReservation).where(BudgetReservation.status == "reserved")
        )
    ).all()
    assert len(active_reservations_3) == 0, "Clarification turn must not lock budget quota"

    refreshed_month_3 = await async_session.get(BudgetAccount, current_month_scope)
    assert refreshed_month_3 is not None
    assert refreshed_month_3.reserved_usd == Decimal("0E-8")
    assert refreshed_month_3.spent_usd == Decimal("0E-8")


@pytest.mark.asyncio
async def test_settle_accurately_updates_spent_and_removes_reserved_multi_scope(
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Challenge 2.1:
    Verify that settle:
    1. Accurately updates spent_usd across all tied scopes.
    2. Accurately removes reserved_usd to 0.
    3. Does not leak any microdollar / fractional cent.
    4. Preserves exact high-precision decimal accounting (8 decimal places).
    """
    repo = BudgetRepository(async_session)

    scope_month = current_month_scope
    scope_proj = "project:challenger-p1"
    scope_task = "task:challenger-t1"

    scopes = {
        scope_month: Decimal("1000.00"),
        scope_proj: Decimal("250.00"),
        scope_task: Decimal("50.00"),
    }

    # Pre-reserve $10.00 across all 3 scopes
    reservation = await repo.reserve(
        idempotency_key="challenger-settle-precision-test",
        reserved_usd=Decimal("10.00"),
        scope_caps=scopes,
    )

    # Check reserved state on all accounts
    for scope_key in scopes:
        acc = await async_session.get(BudgetAccount, scope_key)
        assert acc is not None
        assert acc.reserved_usd == Decimal("10.00000000")
        assert acc.spent_usd == Decimal("0E-8")

    # Settle with high-precision actual expenditure: $3.14159265
    actual_cost = Decimal("3.14159265")
    settled = await repo.settle(
        reservation.id,
        actual_usd=actual_cost,
        usage_receipt={"prompt_tokens": 1234, "completion_tokens": 567, "model": "test-llm"},
    )

    assert settled.status == "settled"
    assert settled.actual_usd == actual_cost
    assert settled.usage_receipt["prompt_tokens"] == 1234

    # Verify each account has exactly reserved_usd=0 and spent_usd=3.14159265
    for scope_key, cap in scopes.items():
        acc = await async_session.get(BudgetAccount, scope_key)
        assert acc is not None
        assert acc.reserved_usd == Decimal("0E-8"), f"Reserved quota not zeroed on {scope_key}"
        assert acc.spent_usd == actual_cost, f"Spent quota incorrect on {scope_key}"

        # Headroom verification
        available = await repo.get_available_quota(scope_key)
        expected_available = cap - actual_cost
        assert available == expected_available, (
            f"Headroom leaked on {scope_key}! Expected {expected_available}, got {available}"
        )


@pytest.mark.asyncio
async def test_settle_overrun_accounting(
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Challenge 2.2:
    Verify settle behavior when actual expenditure exceeds reserved estimate (overrun):
    1. Reservation status transitions to 'overrun'.
    2. Reserved quota is cleared to 0.
    3. Spent quota records the full overrun amount.
    4. Headroom correctly reflects the full actual spend.
    """
    repo = BudgetRepository(async_session)
    scopes = {current_month_scope: Decimal("50.00")}

    reservation = await repo.reserve(
        idempotency_key="challenger-overrun-key",
        reserved_usd=Decimal("2.00"),
        scope_caps=scopes,
    )
    assert reservation.status == "reserved"

    # Actual spend exceeds reserved by $3.50 ($5.50 total)
    actual_overrun = Decimal("5.50")
    settled = await repo.settle(
        reservation.id,
        actual_usd=actual_overrun,
        usage_receipt={"reported_overrun": True, "tokens": 9999},
    )

    assert settled.status == "overrun"
    assert settled.actual_usd == Decimal("5.50000000")

    account = await async_session.get(BudgetAccount, current_month_scope)
    assert account is not None
    assert account.reserved_usd == Decimal("0E-8"), "Reserved quota must be cleared even on overrun"
    assert account.spent_usd == Decimal("5.50000000"), "Spent quota must record full actual overrun cost"

    available = await repo.get_available_quota(current_month_scope)
    assert available == Decimal("50.00") - Decimal("5.50000000")


@pytest.mark.asyncio
async def test_settle_idempotency_and_double_spend_prevention(
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Challenge 2.3:
    Verify settle idempotency:
    1. Calling settle a second time with identical usage succeeds and does not double-count spent_usd.
    2. Calling settle a second time with different usage raises ValueError and does not alter spent_usd.
    """
    repo = BudgetRepository(async_session)
    scopes = {current_month_scope: Decimal("100.00")}

    reservation = await repo.reserve(
        idempotency_key="challenger-idempotent-settle",
        reserved_usd=Decimal("5.00"),
        scope_caps=scopes,
    )

    first_settle = await repo.settle(
        reservation.id,
        actual_usd=Decimal("1.50"),
        usage_receipt={"call": 1},
    )
    assert first_settle.status == "settled"

    account_1 = await async_session.get(BudgetAccount, current_month_scope)
    assert account_1 is not None
    assert account_1.spent_usd == Decimal("1.50000000")

    # Repeat with identical usage: must be idempotent
    second_settle = await repo.settle(
        reservation.id,
        actual_usd=Decimal("1.50"),
        usage_receipt={"call": 1},
    )
    assert second_settle.id == first_settle.id

    account_2 = await async_session.get(BudgetAccount, current_month_scope)
    assert account_2 is not None
    assert account_2.spent_usd == Decimal("1.50000000"), "Idempotent settle must not double count spent_usd"

    # Repeat with conflicting usage: must raise ValueError
    with pytest.raises(ValueError, match="reservation was already settled with different usage"):
        await repo.settle(
            reservation.id,
            actual_usd=Decimal("2.00"),
            usage_receipt={"call": 2},
        )

    account_3 = await async_session.get(BudgetAccount, current_month_scope)
    assert account_3 is not None
    assert account_3.spent_usd == Decimal("1.50000000"), "Conflicting settle must not mutate spent_usd"


@pytest.mark.asyncio
async def test_settle_and_release_state_machine_cross_rejection(
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Challenge 2.4:
    Verify state machine integrity:
    1. Cannot settle a reservation that was released.
    2. Cannot release a reservation that was settled.
    3. Settle rejects negative, NaN, Inf, and boolean values.
    4. Release rejects empty or whitespace reasons.
    """
    repo = BudgetRepository(async_session)
    scopes = {current_month_scope: Decimal("200.00")}

    # Case A: Released reservation cannot be settled
    res_a = await repo.reserve(
        idempotency_key="state-machine-a",
        reserved_usd=Decimal("4.00"),
        scope_caps=scopes,
    )
    await repo.release_after_confirmed_no_charge(
        res_a.id,
        provider_confirmed_unbilled=True,
        reason="No charge confirmed",
    )
    with pytest.raises(ValueError, match="cannot settle reservation in state 'released'"):
        await repo.settle(res_a.id, actual_usd=Decimal("1.00"), usage_receipt={})

    # Case B: Settled reservation cannot be released
    res_b = await repo.reserve(
        idempotency_key="state-machine-b",
        reserved_usd=Decimal("4.00"),
        scope_caps=scopes,
    )
    await repo.settle(res_b.id, actual_usd=Decimal("2.00"), usage_receipt={})
    with pytest.raises(ValueError, match="cannot release reservation in state 'settled'"):
        await repo.release_after_confirmed_no_charge(
            res_b.id,
            provider_confirmed_unbilled=True,
            reason="Attempting to release after settle",
        )

    # Case C: Invalid values in settle
    res_c = await repo.reserve(
        idempotency_key="state-machine-c",
        reserved_usd=Decimal("4.00"),
        scope_caps=scopes,
    )
    for bad_actual in (-1.0, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            await repo.settle(res_c.id, actual_usd=bad_actual, usage_receipt={})

    # Case D: Empty or whitespace release reasons
    for bad_reason in ("", "   ", "\t\n"):
        with pytest.raises(ValueError, match="release reason must contain 1-2000 characters"):
            await repo.release_after_confirmed_no_charge(
                res_c.id,
                provider_confirmed_unbilled=True,
                reason=bad_reason,
            )


@pytest.mark.asyncio
async def test_challenge_cancelled_error_handling(
    async_session: AsyncSession,
    current_month_scope: str,
    monkeypatch,
):
    """
    Challenge 1.3:
    What happens if an asyncio task is cancelled (asyncio.CancelledError) while model planning is in-flight?
    Does the reservation get released, or does it remain orphaned as status='reserved'?
    """
    account = BudgetAccount(
        scope_key=current_month_scope,
        cap_usd=Decimal("500.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    p_repo = ProjectRepository(async_session)
    t_repo = TaskRepository(async_session)
    brain = BossBrain(project_repo=p_repo, task_repo=t_repo)

    async def cancelled_decompose(*args, **kwargs):
        raise asyncio.CancelledError("Task was cancelled by caller")

    monkeypatch.setattr(TaskPlanner, "decompose_objective", cancelled_decompose)

    with pytest.raises(asyncio.CancelledError):
        await brain.plan_and_create_project(
            objective="Cancelled objective",
            project_budget_usd=50.0,
        )

    # Inspect reservation status in DB
    reservation = (
        await async_session.scalars(
            select(BudgetReservation)
        )
    ).first()
    month_acc = await async_session.get(BudgetAccount, current_month_scope)

    assert reservation is not None
    assert reservation.status == "released", (
        f"BUG CONFIRMED: Reservation status is {reservation.status!r} (expected 'released'). "
        "except Exception in BossBrain.plan_and_create_project failed to catch asyncio.CancelledError, "
        "leaving an orphaned reservation and locked quota!"
    )
    assert month_acc is not None
    assert month_acc.reserved_usd == Decimal("0E-8"), (
        f"BUG CONFIRMED: Monthly reserved_usd is {month_acc.reserved_usd} (expected 0.00). "
        "Quota leaked on task cancellation!"
    )


@pytest.mark.asyncio
async def test_challenge_asyncio_wait_for_timeout_orphans_reservation(
    async_session: AsyncSession,
    current_month_scope: str,
    monkeypatch,
):
    """
    Challenge 1.4:
    In real-world async frameworks (FastAPI/AnyIO/asyncio), timeouts are frequently implemented via
    asyncio.wait_for(task, timeout=...). When asyncio.wait_for triggers, it cancels the inner coroutine
    by raising asyncio.CancelledError into it.
    Verify whether an external asyncio.wait_for timeout causes an orphaned quota lock!
    """
    account = BudgetAccount(
        scope_key=current_month_scope,
        cap_usd=Decimal("500.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    p_repo = ProjectRepository(async_session)
    t_repo = TaskRepository(async_session)
    brain = BossBrain(project_repo=p_repo, task_repo=t_repo)

    async def hanging_decompose(*args, **kwargs):
        # Hang forever until cancelled by asyncio.wait_for
        await asyncio.sleep(60.0)
        return {}

    monkeypatch.setattr(TaskPlanner, "decompose_objective", hanging_decompose)

    # Wrap planning call in an external asyncio timeout (0.1s)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            brain.plan_and_create_project(
                objective="Hanging planning call",
                project_budget_usd=25.0,
            ),
            timeout=0.1,
        )

    # Check database: reservation was pre-reserved, but when cancelled by wait_for:
    reservation = await async_session.scalar(
        select(BudgetReservation).where(BudgetReservation.idempotency_key.like("plan-pre-reserve:%"))
    )
    print("WAIT_FOR TIMEOUT -> RESERVATION STATUS:", reservation.status if reservation else None)

    month_acc = await async_session.get(BudgetAccount, current_month_scope)
    print("WAIT_FOR TIMEOUT -> RESERVED_USD:", month_acc.reserved_usd if month_acc else None)

    # Invariant: An external timeout must cleanly release quota without leaving orphaned reservations
    assert reservation is not None
    assert reservation.status == "released", (
        f"BUG CONFIRMED: Reservation status is {reservation.status!r} (expected 'released'). "
        "asyncio.wait_for timeout raised CancelledError which bypassed except Exception in BossBrain, "
        "orphaning the pre-reservation!"
    )
    assert month_acc is not None
    assert month_acc.reserved_usd == Decimal("0E-8"), (
        f"BUG CONFIRMED: Monthly reserved_usd is {month_acc.reserved_usd} (expected 0.00). "
        "Quota remained locked after timeout!"
    )
