"""
Unit test suite for Boss Budget Enforcement & Quota Management (Milestone 1 / R1).
File: tests/unit/test_boss_budget.py
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from neural.api.deps import (
    get_container_manager,
    get_db_session,
    require_owner_identity,
)
from neural.api.main import create_app
from neural.boss.brain import BossBrain
from neural.boss.planner import TaskPlanner
from neural.db.models import BudgetAccount, BudgetReservation, Project
from neural.db.repositories import BudgetRepository, ProjectRepository, TaskRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def month_scope_key() -> str:
    return f"organization-month:{datetime.now(timezone.utc):%Y-%m}"


@pytest_asyncio.fixture
async def app_client(async_session: AsyncSession):
    app = create_app()

    async def override_get_db_session():
        yield async_session

    class FakeContainerManager:
        async def get_container_status(self, *args, **kwargs):
            return "RUNNING"

        async def _broker_request(self, *args, **kwargs):
            return {}

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"
    app.dependency_overrides[get_container_manager] = lambda: FakeContainerManager()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_project_budget_usd_accepted_and_validated_positive_finite(
    app_client: AsyncClient,
    async_session: AsyncSession,
    month_scope_key: str,
):
    # Seed the durable monthly cap used by the provider gateway.
    account = BudgetAccount(
        scope_key=month_scope_key,
        cap_usd=Decimal("500.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    # 1. Zero should be rejected with 422
    resp_zero = await app_client.post(
        "/boss/objective",
        json={"objective": "Test zero budget", "project_budget_usd": 0},
    )
    assert resp_zero.status_code == 422, f"Expected 422 for 0, got {resp_zero.status_code}: {resp_zero.text}"

    # 2. Negative value should be rejected with 422
    resp_neg = await app_client.post(
        "/boss/objective",
        json={"objective": "Test negative budget", "project_budget_usd": -10.0},
    )
    assert resp_neg.status_code == 422, f"Expected 422 for -10, got {resp_neg.status_code}: {resp_neg.text}"

    # 3. NaN should be rejected with 422
    resp_nan = await app_client.post(
        "/boss/objective",
        json={"objective": "Test NaN budget", "project_budget_usd": "NaN"},
    )
    assert resp_nan.status_code == 422, f"Expected 422 for NaN, got {resp_nan.status_code}: {resp_nan.text}"

    # 4. Infinity should be rejected with 422
    resp_inf = await app_client.post(
        "/boss/objective",
        json={"objective": "Test Inf budget", "project_budget_usd": "Infinity"},
    )
    assert resp_inf.status_code == 422, f"Expected 422 for Inf, got {resp_inf.status_code}: {resp_inf.text}"

    # 5. Valid positive float accepted with HTTP 200
    resp_valid = await app_client.post(
        "/boss/objective",
        json={"objective": "Build verified utility package", "project_budget_usd": 150.0},
    )
    assert resp_valid.status_code == 200, f"Expected 200 for 150.0, got {resp_valid.status_code}: {resp_valid.text}"
    body = resp_valid.json()
    assert body["id"] is not None
    assert body["status"] in ("in_progress", "pending", "planned", "planning")
    reservations = (await async_session.scalars(select(BudgetReservation))).all()
    assert len(reservations) == 1
    assert reservations[0].status == "released"
    assert reservations[0].scope_keys == [month_scope_key]
    month_account = await async_session.get(BudgetAccount, month_scope_key)
    assert month_account is not None
    assert month_account.reserved_usd == Decimal("0E-8")


@pytest.mark.asyncio
async def test_project_budget_usd_rejected_when_exceeds_monthly_quota(
    app_client: AsyncClient,
    async_session: AsyncSession,
    month_scope_key: str,
):
    # Setup constrained monthly budget account:
    # Cap = $100.00, Reserved = $30.00, Spent = $50.00 -> Headroom = $20.00
    account = BudgetAccount(
        scope_key=month_scope_key,
        cap_usd=Decimal("100.00"),
        reserved_usd=Decimal("30.00"),
        spent_usd=Decimal("50.00"),
    )
    async_session.add(account)
    await async_session.commit()

    # Request budget of $25.00 (which exceeds available $20.00)
    response = await app_client.post(
        "/boss/objective",
        json={"objective": "Exceed monthly quota", "project_budget_usd": 25.0},
    )

    # Must return HTTP 429
    assert response.status_code == 429, f"Expected 429, got {response.status_code}: {response.text}"
    body = response.json()
    detail = body.get("detail", {})
    if isinstance(detail, dict):
        assert detail.get("code") == "budget_limit_exceeded"
    else:
        assert "budget" in str(detail).lower() or "quota" in str(detail).lower()

    # Fail-closed persistence check: no project or new reservations should be created
    projects = (await async_session.scalars(select(Project))).all()
    assert len(projects) == 0, "No project should be persisted when budget quota is exceeded"

    reservations = (await async_session.scalars(select(BudgetReservation))).all()
    assert len(reservations) == 0, "No reservation should be persisted when budget check rejects"

    # Monthly account numbers must remain unaltered
    refreshed_account = await async_session.get(BudgetAccount, month_scope_key)
    assert refreshed_account is not None
    assert refreshed_account.reserved_usd == Decimal("30.00000000")
    assert refreshed_account.spent_usd == Decimal("50.00000000")


@pytest.mark.asyncio
async def test_project_budget_pre_reserves_durable_quota_before_planning(
    async_session: AsyncSession,
    month_scope_key: str,
    monkeypatch,
):
    # Seed month budget account
    account = BudgetAccount(
        scope_key=month_scope_key,
        cap_usd=Decimal("500.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    reservation_verified_during_planning = False
    reservation_scopes: list[str] = []

    async def hooked_decompose(self, objective, db_session=None, project_id=None, budget_usd=None):
        nonlocal reservation_verified_during_planning
        # Query DB inside planning hook: a reservation MUST already exist!
        res = await db_session.scalar(
            select(BudgetReservation).where(BudgetReservation.status == "reserved")
        )
        if res is not None and res.reserved_usd > Decimal("0"):
            reservation_verified_during_planning = True
            reservation_scopes.extend(res.scope_keys)

        return {
            "code_name": "TestPlan",
            "complexity": "low",
            "phases": [
                {
                    "phase_id": "PHASE-01",
                    "title": "Core Phase",
                    "tasks": [
                        {
                            "task_id": "TASK-01",
                            "title": "Task 1",
                            "execution_mode": "terminal",
                            "skills_required": ["Python"],
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(TaskPlanner, "_decompose_with_model", hooked_decompose)
    monkeypatch.setattr(TaskPlanner, "_has_configured_provider", lambda self, gw: True)

    p_repo = ProjectRepository(async_session)
    t_repo = TaskRepository(async_session)
    brain = BossBrain(project_repo=p_repo, task_repo=t_repo)

    # Call plan_and_create_project with project_budget_usd = 50.0
    project, tasks = await brain.plan_and_create_project(
        objective="Verify pre-reservation sequence",
        project_budget_usd=50.0,
    )

    assert reservation_verified_during_planning is True, (
        "BudgetReservation must be durably committed with status='reserved' before model planning executes"
    )
    assert reservation_scopes == [month_scope_key]
    assert project is not None


@pytest.mark.asyncio
async def test_project_budget_settles_tokens_on_successful_planning(
    async_session: AsyncSession,
    month_scope_key: str,
):
    repo = BudgetRepository(async_session)
    scopes = {month_scope_key: Decimal("100.00"), "project:test-proj": Decimal("50.00")}

    # 1. Pre-reserve
    reservation = await repo.reserve(
        idempotency_key="plan-settle-key",
        reserved_usd=Decimal("5.00"),
        scope_caps=scopes,
    )
    assert reservation.status == "reserved"

    month_before = await async_session.get(BudgetAccount, month_scope_key)
    assert month_before is not None
    assert month_before.reserved_usd == Decimal("5.00000000")
    assert month_before.spent_usd == Decimal("0E-8")

    # 2. Settle with actual token expenditure ($0.15)
    settled = await repo.settle(
        reservation.id,
        actual_usd=Decimal("0.15"),
        usage_receipt={"provider": "mock-llm", "input_tokens": 120, "output_tokens": 30},
    )

    # 3. Verify settlement record
    assert settled.status == "settled"
    assert settled.actual_usd == Decimal("0.15000000")
    assert settled.usage_receipt["provider"] == "mock-llm"

    # 4. Verify accounts: reserved is cleared (0), spent is incremented ($0.15)
    month_after = await async_session.get(BudgetAccount, month_scope_key)
    assert month_after is not None
    assert month_after.reserved_usd == Decimal("0E-8"), "Reserved amount should be cleared after settlement"
    assert month_after.spent_usd == Decimal("0.15000000"), "Spent amount should reflect actual token cost"

    # 5. Verify remaining headroom increased by freed unused reservation ($5.00 - $0.15 = $4.85 returned)
    available = await repo.get_available_quota(month_scope_key)
    assert available == Decimal("100.00") - Decimal("0.15000000")


@pytest.mark.asyncio
async def test_project_budget_releases_reservation_on_planning_failure(
    async_session: AsyncSession,
    month_scope_key: str,
):
    repo = BudgetRepository(async_session)
    scopes = {month_scope_key: Decimal("200.00")}

    # Pre-reserve quota
    reservation = await repo.reserve(
        idempotency_key="plan-failure-key",
        reserved_usd=Decimal("10.00"),
        scope_caps=scopes,
    )
    assert reservation.status == "reserved"

    month_reserved = await async_session.get(BudgetAccount, month_scope_key)
    assert month_reserved is not None
    assert month_reserved.reserved_usd == Decimal("10.00000000")

    # Ambiguous release must be rejected (provider_confirmed_unbilled=False)
    with pytest.raises(ValueError, match="ambiguous provider outcomes"):
        await repo.release_after_confirmed_no_charge(
            reservation.id,
            provider_confirmed_unbilled=False,
            reason="Uncertain network drop",
        )

    # Release with confirmed no charge
    released = await repo.release_after_confirmed_no_charge(
        reservation.id,
        provider_confirmed_unbilled=True,
        reason="Planning aborted before provider invocation",
    )

    assert released.status == "released"
    assert "aborted" in (released.release_reason or "")

    # Verify quota restored
    month_released = await async_session.get(BudgetAccount, month_scope_key)
    assert month_released is not None
    assert month_released.reserved_usd == Decimal("0E-8"), "Reserved quota must be fully zeroed on release"
    assert month_released.spent_usd == Decimal("0E-8"), "No spend should be charged on released failure"

    available = await repo.get_available_quota(month_scope_key)
    assert available == Decimal("200.00000000")


@pytest.mark.asyncio
async def test_project_budget_eliminates_http_501(
    app_client: AsyncClient,
    async_session: AsyncSession,
    month_scope_key: str,
):
    # Seed account
    account = BudgetAccount(
        scope_key=month_scope_key,
        cap_usd=Decimal("1000.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    test_payloads = [
        # Normal positive budget
        {"objective": "Create telemetry pipeline", "project_budget_usd": 50.0},
        # Zero budget
        {"objective": "Create telemetry pipeline", "project_budget_usd": 0},
        # Exceeding budget
        {"objective": "Create telemetry pipeline", "project_budget_usd": 999999.0},
        # Missing budget
        {"objective": "Create telemetry pipeline"},
        # Aliased parameter name
        {"objective": "Create telemetry pipeline", "budget_usd": 40.0},
    ]

    for payload in test_payloads:
        resp = await app_client.post("/boss/objective", json=payload)
        assert resp.status_code != 501, (
            f"Endpoint returned HTTP 501 (Not Implemented) for payload {payload}. "
            "All budget functionality must be implemented and 501 eliminated."
        )
        assert resp.status_code in (200, 422, 429), (
            f"Expected status code in [200, 422, 429], got {resp.status_code}: {resp.text}"
        )


@pytest.mark.asyncio
async def test_budget_repository_get_account_and_available_quota(
    async_session: AsyncSession,
):
    repo = BudgetRepository(async_session)

    # 1. Non-existent account returns None and default cap
    non_existent = await repo.get_account("non-existent-scope")
    assert non_existent is None

    available_default = await repo.get_available_quota("non-existent-scope", default_cap=Decimal("5000.00"))
    assert available_default == Decimal("5000.00")

    # 2. Existing account calculates headroom accurately
    account = BudgetAccount(
        scope_key="scope:active",
        cap_usd=Decimal("1000.00"),
        reserved_usd=Decimal("200.00"),
        spent_usd=Decimal("300.00"),
    )
    async_session.add(account)
    await async_session.commit()

    fetched = await repo.get_account("scope:active")
    assert fetched is not None
    assert fetched.scope_key == "scope:active"

    available = await repo.get_available_quota("scope:active")
    assert available == Decimal("500.00000000")

    # 3. Headroom clamped to 0 when over capacity
    over_account = BudgetAccount(
        scope_key="scope:over",
        cap_usd=Decimal("100.00"),
        reserved_usd=Decimal("50.00"),
        spent_usd=Decimal("60.00"),
    )
    async_session.add(over_account)
    await async_session.commit()

    over_avail = await repo.get_available_quota("scope:over")
    assert over_avail == Decimal("0")

    # 4. Invalid scope key raises ValueError
    with pytest.raises(ValueError, match="1-192 characters"):
        await repo.get_account("")
    with pytest.raises(ValueError, match="1-192 characters"):
        await repo.get_account("x" * 200)


@pytest.mark.asyncio
async def test_brain_plan_and_create_project_releases_reservation_on_planning_failure(
    async_session: AsyncSession,
    month_scope_key: str,
    monkeypatch,
):
    # Seed month budget account
    account = BudgetAccount(
        scope_key=month_scope_key,
        cap_usd=Decimal("1000.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    async def failing_decompose(self, *args, **kwargs):
        raise ValueError("Simulated model planning failure")

    monkeypatch.setattr(TaskPlanner, "decompose_objective", failing_decompose)

    p_repo = ProjectRepository(async_session)
    t_repo = TaskRepository(async_session)
    brain = BossBrain(project_repo=p_repo, task_repo=t_repo)

    with pytest.raises(ValueError, match="Simulated model planning failure"):
        await brain.plan_and_create_project(
            objective="Failing objective",
            project_budget_usd=75.0,
        )

    # Reservation must be marked "released"
    reservation = await async_session.scalar(
        select(BudgetReservation).where(BudgetReservation.status == "released")
    )
    assert reservation is not None
    assert "Simulated model planning failure" in (reservation.release_reason or "")

    # Headroom must be fully restored
    refreshed_month = await async_session.get(BudgetAccount, month_scope_key)
    assert refreshed_month is not None
    assert refreshed_month.reserved_usd == Decimal("0E-8")
    assert refreshed_month.spent_usd == Decimal("0.00000000")
