from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_evolution_repo,
    get_memory_store,
    get_self_improvement_engine,
)
from neural.api.routes.evolution import serialize_evolution_record
from neural.api.schemas.requests import (
    OptimizePromptRequest,
    StoreMemoryRequest,
    TriggerSelfImprovementRequest,
)
from neural.api.schemas.responses import (
    EvolutionRecordResponse,
    MemoryEntryResponse,
    SelfImprovementCycleResponse,
)
from neural.db.models import MemoryEntry
from neural.db.repositories import EvolutionRepository
from neural.evolution.meta_learning import (
    FailurePattern,
    RecursiveSelfImprovementEngine,
)
from neural.memory.store import OrganizationalMemoryStore

router = APIRouter(prefix="/meta-learning", tags=["Recursive Self-Improvement & Meta-Learning"])


def serialize_memory_entry(e: MemoryEntry) -> MemoryEntryResponse:
    return MemoryEntryResponse(
        id=e.id,
        category=e.category.value if hasattr(e.category, "value") else str(e.category),
        source=e.source,
        content=e.content,
        metadata_json=e.metadata_json or {},
        confidence=e.confidence,
        created_at=e.created_at,
    )


@router.post("/cycle", response_model=SelfImprovementCycleResponse)
async def trigger_self_improvement_cycle(
    req: TriggerSelfImprovementRequest = TriggerSelfImprovementRequest(),
    engine: RecursiveSelfImprovementEngine = Depends(get_self_improvement_engine),
):
    """
    Triggers complete autonomous recursive self-improvement cycle:
    Harvest failure logs -> Cluster patterns -> Author tools / Synthesize methods / Optimize prompts -> Commit to 4-tier memory.
    """
    res = await engine.run_self_improvement_cycle(
        trigger_reason=req.trigger_reason,
        auto_apply=req.auto_apply,
        sample_failures=req.sample_failures,
    )
    return SelfImprovementCycleResponse(**res)


@router.get("/failure-patterns")
async def get_failure_patterns(
    engine: RecursiveSelfImprovementEngine = Depends(get_self_improvement_engine),
):
    """
    Analyzes historical task failure patterns and returns classified anti-patterns with severity and proposed remediation.
    """
    patterns = await engine.pattern_analyzer.analyze_failure_patterns()
    return {"total_patterns": len(patterns), "patterns": [p.to_dict() for p in patterns]}


@router.post("/optimize-prompt")
async def optimize_prompt(
    req: OptimizePromptRequest,
    engine: RecursiveSelfImprovementEngine = Depends(get_self_improvement_engine),
):
    """
    Formulates targeted prompt enhancements from a failure pattern and evaluates improvements.
    """
    mock_pattern = FailurePattern(
        pattern_id="PAT-CUSTOM",
        pattern_type=req.pattern_type,
        frequency=1,
        severity="medium",
        root_cause=f"Recurring operational error: {req.pattern_type}",
        proposed_remediation="Inject behavioral guardrails into prompt.",
        remediation_type="prompt",
    )
    result = engine.prompt_optimizer.optimize_prompt(
        current_prompt=req.current_prompt,
        failure_pattern=mock_pattern,
        target_role=req.target_role,
    )
    return result


@router.get("/memory", response_model=list[MemoryEntryResponse])
async def query_memory(
    query: str | None = Query(None, description="Keyword search in memory content"),
    category: str | None = Query(None, description="Memory category: episode, knowledge, procedure, policy, lesson"),
    source: str | None = Query(None, description="Filter by source"),
    limit: int = Query(50, ge=1, le=100),
    store: OrganizationalMemoryStore = Depends(get_memory_store),
):
    """
    Queries 4-Tier Organizational Memory (Episodic, Semantic, Procedural, Policy).
    """
    entries = await store.query_memory(
        query=query,
        category=category,
        source=source,
        limit=limit,
    )
    return [serialize_memory_entry(e) for e in entries]


@router.post("/memory", response_model=MemoryEntryResponse)
async def store_memory(
    req: StoreMemoryRequest,
    store: OrganizationalMemoryStore = Depends(get_memory_store),
):
    """
    Stores an entry directly into 4-Tier Organizational Memory.
    """
    try:
        entry = await store.store_entry(
            category=req.category,
            source=req.source,
            content=req.content,
            metadata_json=req.metadata_json,
            confidence=req.confidence,
        )
        return serialize_memory_entry(entry)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/memory/stats")
async def get_memory_stats(
    store: OrganizationalMemoryStore = Depends(get_memory_store),
):
    """
    Returns entry counts across all 4 memory tiers.
    """
    stats = await store.get_tier_stats()
    return {"tier_distribution": stats, "total_memories": sum(stats.values())}


@router.get("/history", response_model=list[EvolutionRecordResponse])
async def list_evolution_history(
    limit: int = Query(50, ge=1, le=100),
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """
    Lists evolution records generated by recursive self-improvement and curriculum loops.
    """
    records = await evolution_repo.list_all(limit=limit)
    return [serialize_evolution_record(r) for r in records]
