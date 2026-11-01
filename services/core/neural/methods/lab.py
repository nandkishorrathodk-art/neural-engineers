from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Sequence

from neural.db.models import MethodBenchmarkRecord, MethodRecord
from neural.db.repositories import MethodBenchmarkRepository, MethodRepository
from neural.logger import get_logger
from neural.methods.benchmarks import (
    BenchmarkScenario,
    MethodBenchmarkHarness,
    MethodBenchmarkScorecard,
)
from neural.methods.catalog import MethodologyCatalog
from neural.methods.strategies import get_standard_strategy_blueprint

logger = get_logger("ancos.methods.lab")


class MethodLabEngine:
    """
    Autonomous Method Lab Engine.
    Handles synthesis, formulation, empirical benchmarking, and automated promotion
    of problem-solving methods and algorithmic strategies.
    """

    def __init__(
        self,
        method_repo: MethodRepository | None = None,
        benchmark_repo: MethodBenchmarkRepository | None = None,
        catalog: MethodologyCatalog | None = None,
        harness: MethodBenchmarkHarness | None = None,
    ) -> None:
        self.method_repo = method_repo
        self.benchmark_repo = benchmark_repo
        self.catalog = catalog or MethodologyCatalog(method_repo=method_repo)
        self.harness = harness or MethodBenchmarkHarness()

    async def synthesize_method(
        self,
        name: str,
        objective: str,
        strategy_type: str = "decomposition_parallel",
        domain: str = "engineering",
        steps: list[dict[str, Any]] | None = None,
        version: str = "1.0.0",
        fallback_strategy: dict[str, Any] | None = None,
        baseline_method_id: str | None = None,
        author_id: str = "MethodLabAI",
        tags: list[str] | None = None,
    ) -> MethodRecord:
        logger.info("Synthesizing novel problem-solving method", name=name, strategy=strategy_type)

        effective_steps = steps or get_standard_strategy_blueprint(strategy_type)
        m_id = f"MTH-{name.upper().replace('_', '-')}-{version.replace('.', '-')}-{uuid.uuid4().hex[:4].upper()}"

        if self.method_repo:
            method_rec = await self.method_repo.create_method(
                name=name,
                objective=objective,
                strategy_type=strategy_type,
                steps=effective_steps,
                version=version,
                domain=domain,
                fallback_strategy=fallback_strategy or {},
                baseline_method_id=baseline_method_id,
                author_id=author_id,
                tags=tags or [domain, strategy_type],
                method_id=m_id,
                status="candidate",
            )
        else:
            now = datetime.now(UTC).replace(tzinfo=None)
            method_rec = MethodRecord(
                id=str(uuid.uuid4()),
                method_id=m_id,
                name=name,
                version=version,
                domain=domain,
                objective=objective,
                strategy_type=strategy_type,
                steps=effective_steps,
                fallback_strategy=fallback_strategy or {},
                status="candidate",
                baseline_method_id=baseline_method_id,
                author_id=author_id,
                tags=tags or [domain, strategy_type],
                created_at=now,
                updated_at=now,
            )
            self.catalog.register_in_memory(method_rec)

        return method_rec

    async def benchmark_method(
        self,
        method_id_or_name: str,
        baseline_method_id: str | None = None,
        scenarios: list[BenchmarkScenario] | None = None,
    ) -> tuple[MethodBenchmarkRecord | None, MethodBenchmarkScorecard]:
        method: MethodRecord | None = None
        if self.method_repo:
            method = await self.method_repo.get_by_method_id(method_id_or_name)
            if not method:
                method = await self.method_repo.get_by_id(method_id_or_name)

        if not method:
            method = await self.catalog.get_method(method_id_or_name)

        if not method:
            raise ValueError(f"Method '{method_id_or_name}' not found for benchmarking")

        harness = MethodBenchmarkHarness(scenarios=scenarios) if scenarios else self.harness
        scorecard = await harness.evaluate_method(
            method_id=method.method_id,
            steps=method.steps,
            strategy_type=method.strategy_type,
            baseline_method_id=baseline_method_id or method.baseline_method_id,
        )

        record: MethodBenchmarkRecord | None = None
        if self.benchmark_repo:
            record = await self.benchmark_repo.create_benchmark(
                method_id=method.id,
                benchmark_id=scorecard.benchmark_id,
                baseline_method_id=scorecard.baseline_method_id,
                total_scenarios=scorecard.total_scenarios,
                passed_scenarios=scorecard.passed_scenarios,
                accuracy=scorecard.accuracy,
                avg_latency_ms=scorecard.avg_latency_ms,
                token_efficiency=scorecard.token_efficiency,
                reproduction_rate=scorecard.reproduction_rate,
                composite_score=scorecard.composite_score,
                baseline_accuracy=scorecard.baseline_accuracy,
                baseline_latency_ms=scorecard.baseline_latency_ms,
                baseline_token_efficiency=scorecard.baseline_token_efficiency,
                relative_improvement_pct=scorecard.relative_improvement_pct,
                recommendation=scorecard.recommendation,
                scenario_results=scorecard.scenario_results,
            )

        if self.method_repo:
            new_status = "benchmarked"
            await self.method_repo.update_status(method.id, status=new_status)
        method.status = "benchmarked"

        return record, scorecard

    async def promote_method(
        self,
        method_id_or_name: str,
        force: bool = False,
    ) -> MethodRecord:
        method: MethodRecord | None = None
        if self.method_repo:
            method = await self.method_repo.get_by_method_id(method_id_or_name)
            if not method:
                method = await self.method_repo.get_by_id(method_id_or_name)

        if not method:
            method = await self.catalog.get_method(method_id_or_name)

        if not method:
            raise ValueError(f"Method '{method_id_or_name}' not found")

        if not force:
            # Check latest benchmark recommendation
            latest_rec = "iterate"
            if method.benchmarks:
                latest_rec = method.benchmarks[-1].recommendation
            elif self.benchmark_repo:
                benchmarks = await self.benchmark_repo.list_for_method(method.id)
                if benchmarks:
                    latest_rec = benchmarks[0].recommendation

            if latest_rec != "promote":
                raise ValueError(
                    f"Method '{method.name}' does not meet promotion threshold (recommendation={latest_rec}). Use force=True to override."
                )

        if self.method_repo:
            promoted = await self.method_repo.promote_method(method.method_id)
            if promoted:
                method = promoted
        else:
            method.status = "promoted"
            method.promoted_at = datetime.now(UTC).replace(tzinfo=None)
            self.catalog.register_in_memory(method)

        logger.info("Successfully promoted method into active catalog", method_id=method.method_id, name=method.name)
        return method

    async def list_methods(
        self,
        status: str | None = None,
        domain: str | None = None,
    ) -> Sequence[MethodRecord]:
        if self.method_repo:
            return await self.method_repo.list_methods(status=status, domain=domain)
        return [
            m for m in self.catalog._in_memory_catalog.values()
            if (not status or m.status == status) and (not domain or m.domain == domain)
        ]
