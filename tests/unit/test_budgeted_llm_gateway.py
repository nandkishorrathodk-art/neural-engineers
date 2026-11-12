from datetime import datetime, timezone
from decimal import Decimal

import pytest
from neural.db.models import BudgetAccount, BudgetReservation
from neural.exceptions import ModelProviderError, QuotaExceededError
from neural.llm.budget import load_budget_scope_caps
from neural.llm.gateway import LLMGateway
from sqlalchemy import select


@pytest.mark.asyncio
async def test_budget_scope_loader_uses_durable_monthly_cap(async_session):
    month_scope = f"organization-month:{datetime.now(timezone.utc):%Y-%m}"
    async_session.add(
        BudgetAccount(
            scope_key=month_scope,
            cap_usd=Decimal("1000.00"),
            reserved_usd=Decimal("0.00"),
            spent_usd=Decimal("0.00"),
        )
    )
    await async_session.commit()

    gateway = LLMGateway()
    caps = await load_budget_scope_caps(async_session, gateway.cost_tracker)

    assert caps[month_scope] == Decimal("1000.00")


@pytest.mark.asyncio
async def test_gateway_reserves_before_call_and_settles_successful_provider_usage(async_session):
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return {
            "content": "ok",
            "input_tokens": 5,
            "output_tokens": 3,
            "provider": "test-provider",
        }

    gateway = LLMGateway(provider_invoker=invoke)
    response = await gateway.generate(
        prompt="write a short task plan",
        model="gpt-4o-mini",
        max_tokens=50,
        project_id="project-budgeted",
        task_id="task-budgeted",
        db_session=async_session,
        budget_scope_caps={
            "organization-month:2026-11": Decimal("10"),
            "project:project-budgeted": Decimal("2"),
            "task:task-budgeted": Decimal("1"),
        },
    )

    assert len(calls) == 1
    assert response.content == "ok"
    reservation = await async_session.scalar(select(BudgetReservation))
    assert reservation is not None
    assert reservation.status == "settled"
    assert reservation.actual_usd == Decimal(str(response.cost_usd)).quantize(Decimal("0.00000001"))
    assert reservation.usage_receipt["provider"] == "test-provider"
    accounts = (await async_session.scalars(select(BudgetAccount))).all()
    assert len(accounts) == 3
    assert all(account.reserved_usd == Decimal("0E-8") for account in accounts)
    assert all(account.spent_usd == reservation.actual_usd for account in accounts)


@pytest.mark.asyncio
async def test_gateway_denies_over_cap_before_provider_call(async_session):
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return {"content": "should not run", "input_tokens": 1, "output_tokens": 1}

    gateway = LLMGateway(provider_invoker=invoke)
    with pytest.raises(QuotaExceededError):
        await gateway.generate(
            prompt="write a task plan",
            model="gpt-4o-mini",
            max_tokens=500,
            db_session=async_session,
            budget_scope_caps={"project:too-small": Decimal("0.000001")},
        )

    assert calls == []
    assert await async_session.scalar(select(BudgetReservation)) is None


@pytest.mark.asyncio
async def test_ambiguous_provider_failure_keeps_reservation_and_is_not_retried(async_session):
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        raise ModelProviderError("connection timed out after request send")

    gateway = LLMGateway(provider_invoker=invoke)
    with pytest.raises(ModelProviderError):
        await gateway.generate(
            prompt="write a task plan",
            model="gpt-4o-mini",
            max_tokens=50,
            db_session=async_session,
            budget_scope_caps={"project:uncertain": Decimal("1")},
        )

    assert len(calls) == 1
    reservation = await async_session.scalar(select(BudgetReservation))
    assert reservation is not None and reservation.status == "reserved"
    account = await async_session.get(BudgetAccount, "project:uncertain")
    assert account is not None and account.reserved_usd == reservation.reserved_usd


@pytest.mark.asyncio
async def test_boss_planning_reserves_owner_project_budget_before_inference(async_session, monkeypatch):
    import json

    from neural.boss.planner import TaskPlanner
    from neural.config import get_settings

    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        return {
            "content": json.dumps({
                "code_name": "Budgeted",
                "complexity": "low",
                "requirements": ["Produce one bounded task"],
                "technical_risks": [],
                "mitigation_strategies": [],
                "phases": [{
                    "title": "Implementation",
                    "tasks": [{
                        "task_id": "budgeted-task",
                        "title": "Build the requested feature",
                        "objective": "Implement the requested feature.",
                        "scope": "One implementation task",
                        "role_required": "software-engineer",
                        "skills_required": ["Python"],
                        "execution_mode": "terminal",
                        "dependencies": [],
                        "acceptance_criteria": "A persisted implementation passes independent verification.",
                        "subtasks": [],
                        "estimated_hours": 1,
                        "max_tokens": 4000,
                    }],
                }],
            }),
            "input_tokens": 20,
            "output_tokens": 100,
            "provider": "test-provider",
        }

    gateway = LLMGateway(provider_invoker=invoke)
    monkeypatch.setattr(gateway.settings, "BOSS_LLM_PLANNING_ENABLED", True)
    get_settings.cache_clear()
    planner = TaskPlanner(llm_gateway=gateway)
    project_id = "planned-project-budget-test"

    with pytest.raises(QuotaExceededError):
        await planner.decompose_objective(
            "Implement the requested feature",
            db_session=async_session,
            project_id=project_id,
            budget_usd=0.000001,
        )

    assert calls == []
    assert await async_session.get(BudgetAccount, f"project:{project_id}") is None

    result = await planner.decompose_objective(
        "Implement the requested feature",
        db_session=async_session,
        project_id=project_id,
        budget_usd=5.0,
    )
    account = await async_session.get(BudgetAccount, f"project:{project_id}")
    assert result["plan_source"] == "llm"
    assert len(calls) == 1
    assert account is not None
    assert account.cap_usd == Decimal("5.00000000")
    assert account.reserved_usd == Decimal("0E-8")
    assert account.spent_usd > Decimal("0E-8")
