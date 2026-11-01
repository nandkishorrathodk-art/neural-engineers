from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import get_method_benchmark_repo, get_method_lab, get_method_repo
from neural.api.schemas.requests import (
    BenchmarkMethodRequest,
    PromoteMethodRequest,
    SynthesizeMethodRequest,
)
from neural.api.schemas.responses import (
    MethodBenchmarkResponse,
    MethodDetailResponse,
)
from neural.db.models import MethodBenchmarkRecord, MethodRecord
from neural.db.repositories import MethodBenchmarkRepository, MethodRepository
from neural.methods.benchmarks import BenchmarkUnavailableError
from neural.methods.lab import MethodLabEngine

router = APIRouter(prefix="/methods", tags=["Method Lab & Methodology Catalog"])


def serialize_method(m: MethodRecord) -> MethodDetailResponse:
    benchmarks_count = len(m.benchmarks) if hasattr(m, "benchmarks") and m.benchmarks else 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return MethodDetailResponse(
        id=m.id or m.method_id or str(uuid.uuid4()),
        method_id=m.method_id or "",
        name=m.name or "",
        version=m.version or "1.0.0",
        domain=m.domain or "engineering",
        objective=m.objective or "",
        strategy_type=m.strategy_type or "decomposition_parallel",
        steps=m.steps or [],
        fallback_strategy=m.fallback_strategy or {},
        status=m.status or "candidate",
        baseline_method_id=m.baseline_method_id,
        author_id=m.author_id or "MethodLabAI",
        tags=m.tags or [],
        promoted_at=m.promoted_at,
        created_at=m.created_at or now,
        benchmarks_count=benchmarks_count,
    )


def serialize_benchmark(b: MethodBenchmarkRecord) -> MethodBenchmarkResponse:
    return MethodBenchmarkResponse(
        id=b.id,
        benchmark_id=b.benchmark_id,
        method_id=b.method_id,
        evaluation_status=b.evaluation_status,
        total_scenarios=b.total_scenarios,
        passed_scenarios=b.passed_scenarios,
        accuracy=b.accuracy,
        avg_latency_ms=b.avg_latency_ms,
        token_efficiency=b.token_efficiency,
        reproduction_rate=b.reproduction_rate,
        composite_score=b.composite_score,
        baseline_accuracy=b.baseline_accuracy,
        baseline_latency_ms=b.baseline_latency_ms,
        baseline_token_efficiency=b.baseline_token_efficiency,
        relative_improvement_pct=b.relative_improvement_pct,
        recommendation=b.recommendation,
        scenario_results=b.scenario_results or [],
        recorded_at=b.recorded_at,
    )


@router.post("/synthesize", response_model=MethodDetailResponse)
async def synthesize_method(
    req: SynthesizeMethodRequest,
    engine: MethodLabEngine = Depends(get_method_lab),
):
    """
    Synthesizes and formulates a novel multi-step problem-solving method.
    """
    method = await engine.synthesize_method(
        name=req.name,
        objective=req.objective,
        strategy_type=req.strategy_type,
        domain=req.domain,
        steps=req.steps,
        version=req.version,
        baseline_method_id=req.baseline_method_id,
        author_id=req.author_id,
        tags=req.tags,
    )
    return serialize_method(method)


@router.post("/{method_id_or_name}/benchmark", response_model=MethodBenchmarkResponse)
async def benchmark_method(
    method_id_or_name: str,
    req: BenchmarkMethodRequest = BenchmarkMethodRequest(),
    engine: MethodLabEngine = Depends(get_method_lab),
    bmk_repo: MethodBenchmarkRepository = Depends(get_method_benchmark_repo),
):
    """
    Runs the method benchmark only when an executor-backed runner is configured.
    """
    try:
        record, scorecard = await engine.benchmark_method(
            method_id_or_name=method_id_or_name,
            baseline_method_id=req.baseline_method_id,
        )
    except BenchmarkUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if record:
        return serialize_benchmark(record)

    # In-memory return fallback
    return MethodBenchmarkResponse(
        id=scorecard.benchmark_id,
        benchmark_id=scorecard.benchmark_id,
        method_id=scorecard.method_id,
        evaluation_status="not_evaluated",
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
        recorded_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


@router.post("/{method_id_or_name}/promote", response_model=MethodDetailResponse)
async def promote_method(
    method_id_or_name: str,
    req: PromoteMethodRequest = PromoteMethodRequest(),
    engine: MethodLabEngine = Depends(get_method_lab),
):
    """
    Promotes a verified, high-performing problem-solving method into the active catalog.
    """
    try:
        method = await engine.promote_method(method_id_or_name=method_id_or_name, force=req.force)
        return serialize_method(method)
    except BenchmarkUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/catalog", response_model=list[MethodDetailResponse])
async def get_methodology_catalog(
    domain: str | None = Query(None, description="Filter active catalog by domain"),
    engine: MethodLabEngine = Depends(get_method_lab),
):
    """
    Retrieves all active, promoted methods in the organizational methodology catalog.
    """
    methods = await engine.catalog.list_active_methods(domain=domain)
    return [serialize_method(m) for m in methods]


@router.get("/{method_id_or_name}", response_model=MethodDetailResponse)
async def get_method_details(
    method_id_or_name: str,
    engine: MethodLabEngine = Depends(get_method_lab),
    method_repo: MethodRepository = Depends(get_method_repo),
):
    """
    Retrieves full specification and benchmarks for a problem-solving method.
    """
    method = await method_repo.get_by_method_id(method_id_or_name)
    if not method:
        method = await method_repo.get_by_id(method_id_or_name)
    if not method:
        method = await engine.catalog.get_method(method_id_or_name)

    if not method:
        raise HTTPException(status_code=404, detail=f"Method '{method_id_or_name}' not found")

    return serialize_method(method)


@router.get("", response_model=list[MethodDetailResponse])
async def list_methods(
    status: str | None = Query(None, description="Filter by status (candidate, benchmarked, promoted)"),
    domain: str | None = Query(None, description="Filter by domain"),
    engine: MethodLabEngine = Depends(get_method_lab),
):
    """
    Lists all synthesized methods across all lifecycle states.
    """
    methods = await engine.list_methods(status=status, domain=domain)
    return [serialize_method(m) for m in methods]

