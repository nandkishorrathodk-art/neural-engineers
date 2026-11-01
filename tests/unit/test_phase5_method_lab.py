import pytest
from neural.db.repositories import MethodBenchmarkRepository, MethodRepository
from neural.methods.benchmarks import MethodBenchmarkHarness
from neural.methods.catalog import MethodologyCatalog
from neural.methods.lab import MethodLabEngine
from neural.methods.strategies import StrategyType, get_standard_strategy_blueprint
from sqlalchemy.ext.asyncio import AsyncSession


def test_strategy_blueprints():
    cov_steps = get_standard_strategy_blueprint(StrategyType.CHAIN_OF_VERIFICATION)
    assert len(cov_steps) == 4
    assert any("Verification" in s["name"] for s in cov_steps)

    spec_steps = get_standard_strategy_blueprint(StrategyType.SPEC_DRIVEN_SYNTHESIS)
    assert len(spec_steps) == 4
    assert any("Specification" in s["name"] for s in spec_steps)

    debug_steps = get_standard_strategy_blueprint(StrategyType.HYPOTHESIS_DRIVEN_DEBUGGING)
    assert len(debug_steps) == 4
    assert any("Hypotheses" in s["name"] for s in debug_steps)


@pytest.mark.asyncio
async def test_method_lab_synthesis_and_benchmarking(async_session: AsyncSession):
    method_repo = MethodRepository(async_session)
    bmk_repo = MethodBenchmarkRepository(async_session)
    catalog = MethodologyCatalog(method_repo=method_repo)
    engine = MethodLabEngine(method_repo=method_repo, benchmark_repo=bmk_repo, catalog=catalog)

    # 1. Synthesize candidate method
    method = await engine.synthesize_method(
        name="spec_driven_codegen",
        objective="Generate high-reliability API code with zero hallucinations",
        strategy_type="spec_driven_synthesis",
        domain="backend_engineering",
    )
    assert method.status == "candidate"
    assert method.domain == "backend_engineering"
    assert len(method.steps) == 4

    # 2. Benchmark candidate method
    record, scorecard = await engine.benchmark_method(method.method_id)
    assert scorecard.total_scenarios == 4
    assert scorecard.passed_scenarios >= 3
    assert scorecard.accuracy >= 0.75
    assert scorecard.token_efficiency > 0.0
    assert scorecard.reproduction_rate >= 0.90
    assert scorecard.composite_score > 0.0
    assert scorecard.recommendation in ("promote", "iterate")

    assert record is not None
    assert record.accuracy == scorecard.accuracy
    assert record.composite_score == scorecard.composite_score


@pytest.mark.asyncio
async def test_method_promotion_lifecycle(async_session: AsyncSession):
    method_repo = MethodRepository(async_session)
    bmk_repo = MethodBenchmarkRepository(async_session)
    catalog = MethodologyCatalog(method_repo=method_repo)
    engine = MethodLabEngine(method_repo=method_repo, benchmark_repo=bmk_repo, catalog=catalog)

    method = await engine.synthesize_method(
        name="chain_of_verification_fastapi",
        objective="Verify facts before generating endpoints",
        strategy_type="chain_of_verification",
    )

    # Candidate should not be in active catalog yet
    active_before = await catalog.list_active_methods()
    assert not any(m.method_id == method.method_id for m in active_before)

    # Benchmark method
    await engine.benchmark_method(method.method_id)

    # Promote to active catalog
    promoted = await engine.promote_method(method.method_id, force=True)
    assert promoted.status == "promoted"
    assert promoted.promoted_at is not None

    # Now should appear in active catalog
    active_after = await catalog.list_active_methods()
    assert any(m.method_id == method.method_id for m in active_after)

    # Execution plan retrieval
    plan = await catalog.get_execution_plan(method.method_id)
    assert plan is not None
    assert plan["total_steps"] == 4
    assert plan["name"] == "chain_of_verification_fastapi"


@pytest.mark.asyncio
async def test_method_benchmark_harness_metrics():
    harness = MethodBenchmarkHarness()
    cov_steps = get_standard_strategy_blueprint(StrategyType.CHAIN_OF_VERIFICATION)

    scorecard = await harness.evaluate_method(
        method_id="MTH-TEST-HARNESS",
        steps=cov_steps,
        strategy_type="chain_of_verification",
        baseline_metrics={"accuracy": 0.65, "avg_latency_ms": 300.0, "token_efficiency": 0.70},
    )

    assert scorecard.accuracy >= 0.75
    assert scorecard.avg_latency_ms > 0.0
    assert scorecard.reproduction_rate >= 0.90
    assert len(scorecard.scenario_results) == 4
    assert scorecard.recommendation == "promote"


@pytest.mark.asyncio
async def test_method_benchmark_edge_cases_zero_scenarios_and_degenerate_steps():
    # 1. Zero scenarios should return 0 metrics without ZeroDivisionError
    empty_harness = MethodBenchmarkHarness(scenarios=[])
    card = await empty_harness.evaluate_method("MTH-EMPTY", steps=[], strategy_type="iterative_refinement")
    assert card.total_scenarios == 0
    assert card.accuracy == 0.0
    assert card.composite_score == 0.0
    assert card.recommendation == "reject"

    # 2. Degenerate steps (empty dict, non-dict, or missing keys)
    standard_harness = MethodBenchmarkHarness()
    degenerate_steps = [{}, {"name": "EmptyStep"}, "invalid_step_format"]
    card_deg = await standard_harness.evaluate_method("MTH-DEG", steps=degenerate_steps, strategy_type="iterative_refinement")
    assert card_deg.total_scenarios == 4
    assert card_deg.recommendation in ("iterate", "reject")


@pytest.mark.asyncio
async def test_method_catalog_and_benchmark_repo_id_resolution(async_session: AsyncSession):
    method_repo = MethodRepository(async_session)
    bmk_repo = MethodBenchmarkRepository(async_session)
    catalog = MethodologyCatalog(method_repo=method_repo)
    engine = MethodLabEngine(method_repo=method_repo, benchmark_repo=bmk_repo, catalog=catalog)

    # Candidate method (not promoted yet)
    method = await engine.synthesize_method(
        name="lookup_candidate_test",
        objective="Ensure candidates can be found by name",
        strategy_type="hypothesis_driven_debugging",
    )

    # Should find candidate by name when promoted_only is False
    found = await catalog.get_method("lookup_candidate_test", promoted_only=False)
    assert found is not None
    assert found.method_id == method.method_id

    # Benchmark method
    record, _ = await engine.benchmark_method("lookup_candidate_test")
    assert record is not None

    # Benchmark repository should resolve benchmarks by human-readable method_id string as well
    benchmarks = await bmk_repo.list_for_method(method.method_id)
    assert len(benchmarks) >= 1
    assert benchmarks[0].method_id == method.id

