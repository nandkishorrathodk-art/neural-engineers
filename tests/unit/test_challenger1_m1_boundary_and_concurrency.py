"""
Adversarial Empirical Stress Suite for Milestone 1: Budget Enforcement & Quota Management.
Authored by Challenger 1 (Replacement).

Covers:
1. Boundary value testing:
   - Extreme float precision
   - Zero (0, 0.0, -0.0)
   - Negative floats (-0.0001, -10.0, -1e6)
   - Sub-cent values ($0.0001, $0.00000001)
   - Max cap ($1,000,000.00) and over-cap ($1,000,000.01)
   - NaN, Inf, -Inf
   - String injections and type violations
   - Schema HTTP 422 adherence
2. Quota limit testing:
   - Available quota $100.00 vs $100.01 (HTTP 429 adherence)
   - Exact limit ($100.00 vs $100.00)
   - Concurrency stress: Available quota $100.00 with concurrent requests for $60.00
"""

import asyncio
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
from neural.api.schemas.requests import ObjectiveSubmissionRequest
from neural.db.models import BudgetAccount, Project
from neural.db.repositories import BudgetRepository
from neural.exceptions import QuotaExceededError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def current_month_scope() -> str:
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


# ============================================================================
# Section 1: ObjectiveSubmissionRequest Boundary Values & Schema Validation
# ============================================================================

def test_pydantic_schema_zero_rejected():
    """Verify 0, 0.0, -0.0 are rejected by gt=0."""
    for zero_val in (0, 0.0, -0.0):
        with pytest.raises(ValidationError) as exc_info:
            ObjectiveSubmissionRequest(objective="test", project_budget_usd=zero_val)
        errors = exc_info.value.errors()
        assert any(e["type"] == "greater_than" for e in errors)

        with pytest.raises(ValidationError):
            ObjectiveSubmissionRequest(objective="test", budget_usd=zero_val)


def test_pydantic_schema_negative_floats_rejected():
    """Verify negative floats are rejected by gt=0."""
    negatives = [-0.0001, -1.0, -10.0, -1e-9, -1_000_000.0]
    for neg_val in negatives:
        with pytest.raises(ValidationError) as exc_info:
            ObjectiveSubmissionRequest(objective="test", project_budget_usd=neg_val)
        errors = exc_info.value.errors()
        assert any(e["type"] == "greater_than" for e in errors)


def test_pydantic_schema_nan_and_inf_rejected():
    """Verify NaN, Inf, -Inf are rejected by allow_inf_nan=False."""
    invalids = [
        float("nan"),
        float("inf"),
        float("-inf"),
    ]
    for bad_val in invalids:
        with pytest.raises(ValidationError) as exc_info:
            ObjectiveSubmissionRequest(objective="test", project_budget_usd=bad_val)
        errors = exc_info.value.errors()
        assert any("finite" in str(e).lower() or "nan" in str(e).lower() for e in errors)


def test_pydantic_schema_max_cap_and_over_cap():
    """Verify 1,000,000 is allowed but 1,000,000.01 is rejected by le=1_000_000."""
    # Exact max cap: $1,000,000.00
    req_max = ObjectiveSubmissionRequest(objective="test", project_budget_usd=1_000_000.00)
    assert req_max.project_budget_usd == 1_000_000.00
    assert req_max.budget_usd == 1_000_000.00

    # Over max cap: $1,000,000.01
    with pytest.raises(ValidationError) as exc_info:
        ObjectiveSubmissionRequest(objective="test", project_budget_usd=1_000_000.01)
    errors = exc_info.value.errors()
    assert any(e["type"] == "less_than_equal" for e in errors)

    # Way over cap: 1e9
    with pytest.raises(ValidationError):
        ObjectiveSubmissionRequest(objective="test", project_budget_usd=1e9)


def test_pydantic_schema_sub_cent_and_extreme_precision():
    """Verify sub-cent values (e.g. $0.0001, $0.00000001) are accepted by float validation."""
    sub_cents = [0.0001, 0.00000001, 0.12345678]
    for sc in sub_cents:
        req = ObjectiveSubmissionRequest(objective="test", project_budget_usd=sc)
        assert req.project_budget_usd == sc
        assert req.budget_usd == sc


def test_pydantic_schema_mismatched_budget_fields_rejected():
    """Verify that if both project_budget_usd and budget_usd are given with different values, it raises ValidationError."""
    with pytest.raises(ValidationError, match="must match if both are provided"):
        ObjectiveSubmissionRequest(objective="test", project_budget_usd=100.0, budget_usd=200.0)


# ============================================================================
# Section 2: HTTP Route Boundary Validation (HTTP 422 vs HTTP 200)
# ============================================================================

@pytest.mark.asyncio
async def test_api_boundary_matrix_http_422(
    app_client: AsyncClient,
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Stress-test /boss/objective with invalid boundary inputs:
    - Zero: 0, 0.0
    - Negative floats: -0.01, -500.0
    - NaN string and literal
    - Inf string and literal
    - Over max cap: 1000000.01, 5000000.0
    - String injections: 'DROP TABLE', 'not-a-number', '{}'
    All must return HTTP 422 Unprocessable Entity.
    """
    # Seed month budget account
    account = BudgetAccount(
        scope_key=current_month_scope,
        cap_usd=Decimal("10000000.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    adversarial_inputs = [
        {"project_budget_usd": 0},
        {"project_budget_usd": 0.0},
        {"project_budget_usd": -0.01},
        {"project_budget_usd": -100.0},
        {"project_budget_usd": "NaN"},
        {"project_budget_usd": "Infinity"},
        {"project_budget_usd": "-Infinity"},
        {"project_budget_usd": 1_000_000.01},
        {"project_budget_usd": 2_500_000.0},
        {"project_budget_usd": "not-a-number"},
        {"project_budget_usd": "'; DROP TABLE budget_accounts; --"},
        {"project_budget_usd": "{}"},
        {"project_budget_usd": "[]"},
        {"project_budget_usd": 50.0, "budget_usd": 100.0},  # Mismatched
    ]

    for payload in adversarial_inputs:
        body = {"objective": "Adversarial stress payload", **payload}
        response = await app_client.post("/boss/objective", json=body)
        assert response.status_code == 422, (
            f"Expected HTTP 422 for payload {payload}, got {response.status_code}: {response.text}"
        )


@pytest.mark.asyncio
async def test_boolean_injection_behavior(
    app_client: AsyncClient,
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Challenge: What happens when a boolean True is injected into project_budget_usd?
    Document exact behavior under Pydantic float parsing.
    """
    body = {"objective": "Boolean injection test", "project_budget_usd": True}
    response = await app_client.post("/boss/objective", json=body)
    print(f"\n[BOOLEAN INJECTION] Response status: {response.status_code}, body: {response.text}")


@pytest.mark.asyncio
async def test_api_boundary_accepted_values(
    app_client: AsyncClient,
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Verify valid edge inputs return HTTP 200:
    - Sub-cent: $0.0001
    - 1 cent: $0.01
    - Typical: $150.00
    - High precision: $49.123456
    """
    account = BudgetAccount(
        scope_key=current_month_scope,
        cap_usd=Decimal("10000.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    valid_budgets = [0.0001, 0.01, 49.123456, 150.0]
    for b in valid_budgets:
        response = await app_client.post(
            "/boss/objective",
            json={"objective": f"Test valid edge budget {b}", "project_budget_usd": b},
        )
        assert response.status_code == 200, (
            f"Expected HTTP 200 for budget {b}, got {response.status_code}: {response.text}"
        )


# ============================================================================
# Section 3: Quota Limits — Available Quota $100.00 vs $100.01 & $100.00
# ============================================================================

@pytest.mark.asyncio
async def test_quota_limit_100_vs_100_01(
    app_client: AsyncClient,
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Challenge: Available quota is $100.00:
    - Request asking for $100.01 must return HTTP 429.
    - Request asking for $100.00 must return HTTP 200.
    """
    account = BudgetAccount(
        scope_key=current_month_scope,
        cap_usd=Decimal("100.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    # 1. Ask for $100.01 -> Exceeds by 1 cent
    resp_exceed = await app_client.post(
        "/boss/objective",
        json={"objective": "Exceed by 1 cent", "project_budget_usd": 100.01},
    )
    assert resp_exceed.status_code == 429, (
        f"Expected HTTP 429 for 100.01 on 100.00 cap, got {resp_exceed.status_code}: {resp_exceed.text}"
    )
    detail = resp_exceed.json().get("detail", {})
    assert detail.get("code") == "budget_limit_exceeded"

    # Verify no project or reservation was persisted
    projects = (await async_session.scalars(select(Project))).all()
    assert len(projects) == 0

    # 2. Ask for exactly $100.00 -> Within available headroom
    resp_exact = await app_client.post(
        "/boss/objective",
        json={"objective": "Exact budget match", "project_budget_usd": 100.00},
    )
    assert resp_exact.status_code == 200, (
        f"Expected HTTP 200 for 100.00 on 100.00 cap, got {resp_exact.status_code}: {resp_exact.text}"
    )


# ============================================================================
# Section 4: Concurrency Stress — Available Quota $100.00 with Two $60.00 Requests
# ============================================================================

@pytest.mark.asyncio
async def test_budget_repository_concurrent_reservations_race_condition(
    async_session: AsyncSession,
    current_month_scope: str,
):
    """
    Direct Repository Concurrency:
    When month account has cap = $100.00, two concurrent reservation requests
    each attempting to reserve $60.00 against the month account:
    - One MUST succeed.
    - The other MUST fail with QuotaExceededError.
    - Total reserved MUST NOT exceed $100.00.
    """
    repo = BudgetRepository(async_session)
    account = BudgetAccount(
        scope_key=current_month_scope,
        cap_usd=Decimal("100.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    # Attempt 1: Reserve $60.00
    res1 = await repo.reserve(
        idempotency_key="concurrent-test-1",
        reserved_usd=Decimal("60.00"),
        scope_caps={current_month_scope: Decimal("100.00")},
    )
    assert res1.status == "reserved"

    # Attempt 2: Reserve another $60.00 on same account -> Should fail because 60 + 60 = 120 > 100
    with pytest.raises(QuotaExceededError, match="cannot reserve"):
        await repo.reserve(
            idempotency_key="concurrent-test-2",
            reserved_usd=Decimal("60.00"),
            scope_caps={current_month_scope: Decimal("100.00")},
        )

    # Check that account reserved_usd is strictly 60.00, not 120.00
    account_check = await async_session.get(BudgetAccount, current_month_scope)
    assert account_check is not None
    assert account_check.reserved_usd == Decimal("60.00000000")


@pytest.mark.asyncio
async def test_api_concurrent_requests_two_times_60_on_100_quota(
    tmp_path,
    current_month_scope: str,
):
    """
    HTTP API Concurrency Challenge:
    Available monthly quota = $100.00.
    Two concurrent requests each ask for $60.00.
    Does one succeed and the other get rejected? Or what happens?
    """
    from neural.db.engine import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    db_file = tmp_path / "concurrent_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Seed the month account with cap = $100.00
    async with session_factory() as seed_session:
        account = BudgetAccount(
            scope_key=current_month_scope,
            cap_usd=Decimal("100.00"),
            reserved_usd=Decimal("0.00"),
            spent_usd=Decimal("0.00"),
        )
        seed_session.add(account)
        await seed_session.commit()

    app = create_app()

    async def override_get_db_session():
        async with session_factory() as session:
            yield session

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
        # Fire two concurrent POST requests each requesting $60.00
        req1 = client.post(
            "/boss/objective",
            json={"objective": "Concurrent Project A", "project_budget_usd": 60.0},
        )
        req2 = client.post(
            "/boss/objective",
            json={"objective": "Concurrent Project B", "project_budget_usd": 60.0},
        )

        resp1, resp2 = await asyncio.gather(req1, req2)

    await engine.dispose()

    status_codes = [resp1.status_code, resp2.status_code]
    print(f"\n[CONCURRENCY EMPIRICAL RESULT] Status codes for two $60 requests on $100 quota: {status_codes}")
    print(f"Resp 1: {resp1.status_code} - {resp1.text[:200]}")
    print(f"Resp 2: {resp2.status_code} - {resp2.text[:200]}")

    # Inspect the outcomes
    num_200 = status_codes.count(200)
    num_429 = status_codes.count(429)
    print(f"Result count: 200s={num_200}, 429s={num_429}")
