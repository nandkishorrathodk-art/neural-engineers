import time

import pytest
from neural.db.repositories import EmployeeRepository, PerformanceRepository
from neural.exceptions import ModelProviderError
from neural.llm.cost import CostTracker
from neural.llm.gateway import LLMGateway
from neural.llm.retry import CircuitBreaker, CircuitState, retry_with_backoff
from sqlalchemy.ext.asyncio import AsyncSession


def test_model_routing_config_tiers_and_routes():
    gateway = LLMGateway(config_path="configs/model-routing.yaml")

    # Verify tiers loaded
    assert "reasoning" in gateway.tiers_config
    assert "coding" in gateway.tiers_config
    assert "fast" in gateway.tiers_config

    reasoning_models = gateway.get_models_for_tier("reasoning")
    assert reasoning_models[0].startswith("nim/")
    assert len(reasoning_models) >= 2
    assert reasoning_models[1] == "nim/nvidia/nemotron-3.5-bolt-e4b"
    assert "nemotron-3-super-120b-a12b" not in reasoning_models

    coding_models = gateway.get_models_for_tier("coding")
    assert coding_models[0].startswith("nim/")
    assert len(coding_models) >= 2
    assert coding_models[1] == "nim/nvidia/nemotron-3.5-bolt-e4b"
    assert "nemotron-3-super-120b-a12b" not in coding_models

    fast_models = gateway.get_models_for_tier("fast")
    assert fast_models[0].startswith("nim/")
    assert len(fast_models) >= 2

    # Verify routes mapped to tiers
    assert gateway.resolve_tier_for_route("boss_brain") == "reasoning"
    assert gateway.resolve_tier_for_route("code_generation") == "coding"
    assert gateway.resolve_tier_for_route("status_reporting") == "fast"
    assert gateway.resolve_tier_for_route("independent_verifier") == "reasoning"


@pytest.mark.asyncio
async def test_llm_gateway_transparent_fallback_on_failure():
    """
    If primary model in a tier errors out (e.g., rate limit),
    the gateway must transparently switch to the next fallback model.
    """
    call_log: list[str] = []

    async def mock_invoker(model: str, prompt: str, **kwargs):
        call_log.append(model)
        if model == gateway.get_models_for_tier("reasoning")[0]:
            # Primary model fails with rate limit
            raise ModelProviderError("429 Rate Limit Exceeded on Anthropic API")
        return {
            "content": f"Success from fallback model {model}",
            "input_tokens": 120,
            "output_tokens": 80,
            "provider": "openai"
        }

    gateway = LLMGateway(provider_invoker=mock_invoker)
    response = await gateway.generate(
        prompt="Design high throughput event pipeline",
        tier="reasoning"
    )

    fallback_model = gateway.get_models_for_tier("reasoning")[1]
    assert response.model == fallback_model
    assert f"Success from fallback model {fallback_model}" in response.content
    assert call_log == [gateway.get_models_for_tier("reasoning")[0]] * 2 + [fallback_model]
    assert response.cost_usd > 0.0


@pytest.mark.asyncio
async def test_llm_gateway_tier_exhaustion_raises_model_provider_error():
    """
    If all candidate models in a tier fail, gateway raises ModelProviderError.
    """
    async def always_failing_invoker(model: str, **kwargs):
        raise ModelProviderError(f"Provider {model} is down (503 Service Unavailable)")

    gateway = LLMGateway(provider_invoker=always_failing_invoker)

    with pytest.raises(ModelProviderError) as exc_info:
        await gateway.generate(prompt="Hello", tier="fast")

    assert "All candidate models failed for tier 'fast'" in str(exc_info.value)


def test_circuit_breaker_transitions():
    breaker = CircuitBreaker(
        name="test-provider",
        failure_threshold=3,
        recovery_timeout=0.1
    )

    # 1. Starts CLOSED
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() is True

    # 2. Record 2 failures -> Still CLOSED (< threshold 3)
    breaker.record_failure(ValueError("Error 1"))
    breaker.record_failure(ValueError("Error 2"))
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() is True

    # 3. 3rd failure -> Trips to OPEN
    breaker.record_failure(ValueError("Error 3"))
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() is False

    # 4. Wait for recovery timeout -> Transitions to HALF_OPEN
    time.sleep(0.12)
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.can_execute() is True

    # 5. Success in HALF_OPEN restores to CLOSED
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() is True


def test_circuit_breaker_half_open_failure_re_trips_open(monkeypatch):
    from neural.llm import retry

    clock = [100.0]
    monkeypatch.setattr(retry.time, "monotonic", lambda: clock[0])
    breaker = CircuitBreaker(
        name="test-provider-re-trip",
        failure_threshold=2,
        recovery_timeout=0.05
    )
    breaker.record_failure(ValueError("1"))
    breaker.record_failure(ValueError("2"))
    assert breaker.state == CircuitState.OPEN

    clock[0] += 0.051
    assert breaker.state == CircuitState.HALF_OPEN

    # Failure during trial -> Trips back to OPEN immediately
    breaker.record_failure(ValueError("Trial failed"))
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() is False


@pytest.mark.asyncio
async def test_retry_with_backoff_transient_failure_recovery():
    attempts = 0

    async def transient_operation():
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ConnectionError("Temporary socket timeout")
        return "success"

    result = await retry_with_backoff(
        transient_operation,
        max_retries=2,
        initial_delay=0.01,
        retryable_exceptions=(ConnectionError,)
    )

    assert result == "success"
    assert attempts == 2


@pytest.mark.asyncio
async def test_retry_with_backoff_exhaustion():
    attempts = 0

    async def persistent_failure():
        nonlocal attempts
        attempts += 1
        raise ConnectionError("Fatal network outage")

    with pytest.raises(ConnectionError):
        await retry_with_backoff(
            persistent_failure,
            max_retries=2,
            initial_delay=0.01,
            retryable_exceptions=(ConnectionError,)
        )

    assert attempts == 3  # Initial + 2 retries


def test_cost_tracker_calculation_and_aggregation():
    tracker = CostTracker()

    # 1. Exact calculation
    # gpt-4o: $2.50 input per 1M, $10.00 output per 1M
    # 100,000 input tokens = $0.25, 50,000 output tokens = $0.50 -> $0.75
    cost_gpt4o = tracker.calculate_cost("gpt-4o", 100_000, 50_000)
    assert cost_gpt4o == 0.75

    # claude-3-5-sonnet: $3.00 input per 1M, $15.00 output per 1M
    # 1,000,000 input = $3.00, 1,000,000 output = $15.00 -> $18.00
    cost_claude = tracker.calculate_cost("claude-3-5-sonnet", 1_000_000, 1_000_000)
    assert cost_claude == 18.0

    # Local / open model: $0.00
    cost_local = tracker.calculate_cost("qwen-2.5-coder-32b", 500_000, 500_000)
    assert cost_local == 0.0

    # 2. Record usage by task and employee
    tracker.record_usage(
        model="gpt-4o",
        input_tokens=10_000,
        output_tokens=2_000,
        task_id="TASK-01",
        employee_id="ANC-EMP-ALEX"
    )
    tracker.record_usage(
        model="claude-3-5-sonnet",
        input_tokens=20_000,
        output_tokens=5_000,
        task_id="TASK-01",
        employee_id="ANC-EMP-ALEX"
    )
    tracker.record_usage(
        model="gpt-4o-mini",
        input_tokens=5_000,
        output_tokens=1_000,
        task_id="TASK-02",
        employee_id="ANC-EMP-MAYA"
    )

    task1_stats = tracker.get_task_cost("TASK-01")
    assert task1_stats["call_count"] == 2
    assert task1_stats["input_tokens"] == 30_000
    assert task1_stats["output_tokens"] == 7_000
    assert task1_stats["cost_usd"] > 0

    alex_stats = tracker.get_employee_cost("ANC-EMP-ALEX")
    assert alex_stats["call_count"] == 2
    assert alex_stats["total_tokens"] == 37_000

    maya_stats = tracker.get_employee_cost("ANC-EMP-MAYA")
    assert maya_stats["call_count"] == 1
    assert maya_stats["total_tokens"] == 6_000

    assert tracker.get_total_cost() == round(task1_stats["cost_usd"] + tracker.get_task_cost("TASK-02")["cost_usd"], 6)


@pytest.mark.asyncio
async def test_llm_gateway_records_cost_in_database(async_session: AsyncSession):
    """
    Verifies that calling generate with db_session, employee_id, and task_id
    persists a PerformanceRecord with exact token and USD cost into the database.
    """
    employee_repo = EmployeeRepository(async_session)
    perf_repo = PerformanceRepository(async_session)

    emp = await employee_repo.create(
        employee_id="ANC-EMP-DEV",
        name="Dev User",
        title="Software Engineer",
        system_prompt="...",
        status="idle"
    )

    async def mock_invoker(model: str, prompt: str, **kwargs):
        return {
            "content": "Code review comments: LGTM",
            "input_tokens": 1000,
            "output_tokens": 500,
            "provider": "anthropic"
        }

    gateway = LLMGateway(provider_invoker=mock_invoker)
    res = await gateway.generate(
        prompt="Review this code block",
        route="code_review",
        task_id="TASK-REVIEW-01",
        employee_id=emp.employee_id,
        db_session=async_session
    )

    assert res.content == "Code review comments: LGTM"
    assert res.cost_usd > 0

    # Query DB to verify persistence
    records = await perf_repo.list_by_employee(emp.employee_id)
    assert len(records) == 1
    rec = records[0]
    assert rec.task_id == "TASK-REVIEW-01"
    assert rec.cost_tokens == 1500
    assert rec.cost_usd == res.cost_usd


@pytest.mark.asyncio
async def test_llm_gateway_circuit_breaker_fast_skips_open_models():
    """
    Verifies that if a model's circuit breaker is OPEN, the gateway
    skips calling it entirely and proceeds straight to the fallback.
    """
    call_log = []

    async def mock_invoker(model: str, prompt: str, **kwargs):
        call_log.append(model)
        return {
            "content": f"Result from {model}",
            "input_tokens": 100,
            "output_tokens": 50,
            "provider": "openai"
        }

    gateway = LLMGateway(provider_invoker=mock_invoker)
    # Trip primary model breaker to OPEN manually
    primary_model, fallback_model = gateway.get_models_for_tier("reasoning")[:2]
    breaker = gateway.get_circuit_breaker(primary_model)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    # Generate request for reasoning tier
    res = await gateway.generate(prompt="Quick reasoning", tier="reasoning")

    # claude-3-7-sonnet must NOT be called because circuit breaker was OPEN
    assert primary_model not in call_log
    assert res.model == fallback_model
    assert call_log == [fallback_model]


def test_cost_tracker_unknown_model_fallback():
    tracker = CostTracker()
    # Unknown exotic model should safely use fallback pricing without crashing
    cost = tracker.calculate_cost("custom-proprietary-v1", 1_000_000, 1_000_000)
    assert cost > 0.0

