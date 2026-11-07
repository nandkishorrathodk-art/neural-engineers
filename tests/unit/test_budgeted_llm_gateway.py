from decimal import Decimal

import pytest
from neural.db.models import BudgetAccount, BudgetReservation
from neural.exceptions import ModelProviderError, QuotaExceededError
from neural.llm.gateway import LLMGateway
from sqlalchemy import select


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
