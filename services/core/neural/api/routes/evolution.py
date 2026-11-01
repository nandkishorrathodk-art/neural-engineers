from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_employee_repo,
    get_evolution_repo,
    get_project_repo,
    get_task_repo,
)
from neural.api.schemas.requests import (
    GenerateCurriculumRequest,
    RunSkillGapAnalysisRequest,
    TriggerEvolutionCycleRequest,
)
from neural.api.schemas.responses import (
    CurriculumResponse,
    EvolutionCycleResponse,
    EvolutionRecordResponse,
    SkillGapReportResponse,
)
from neural.db.models import EvolutionRecord
from neural.db.repositories import (
    EmployeeRepository,
    EvolutionRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.evolution.curriculum import CurriculumGenerator
from neural.evolution.gap_analysis import SkillGapAnalyzer
from neural.evolution.lifecycle import SelfEvolutionEngine

router = APIRouter(prefix="/evolution", tags=["Recursive Evolution & Continuous Learning"])


def serialize_evolution_record(r: EvolutionRecord) -> EvolutionRecordResponse:
    return EvolutionRecordResponse(
        id=r.id,
        record_type=r.record_type,
        hypothesis=r.hypothesis,
        experiment_details=r.experiment_details or {},
        baseline_metrics=r.baseline_metrics or {},
        experiment_metrics=r.experiment_metrics or {},
        status=r.status,
        version=r.version,
        created_at=r.created_at,
    )


@router.get("/status")
async def get_evolution_status(
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """Retrieves operational status and summary metrics of the evolution engine."""
    records = await evolution_repo.list_all(limit=100)
    return {
        "status": "active",
        "total_evolution_records": len(records),
        "latest_version": records[0].version if records else "1.0.0",
        "supported_loop_stages": [
            "OBSERVE",
            "IDENTIFY_LIMITATIONS",
            "HYPOTHESIZE",
            "DESIGN_CURRICULUM",
            "EXPERIMENT",
            "BENCHMARK",
            "STAGE_RELEASE",
        ],
    }


@router.post("/gap-analysis", response_model=SkillGapReportResponse)
async def run_skill_gap_analysis(
    req: RunSkillGapAnalysisRequest = RunSkillGapAnalysisRequest(),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    project_repo: ProjectRepository = Depends(get_project_repo),
):
    """
    Executes autonomous skill gap analysis against live tasks and projects.
    Detects capability deficits from failures and unverified workforce claims.
    """
    analyzer = SkillGapAnalyzer(
        employee_repo=employee_repo,
        task_repo=task_repo if req.include_failures else None,
        project_repo=project_repo if req.include_projects else None,
    )
    report = await analyzer.analyze_gaps()
    return SkillGapReportResponse(**report.to_dict())


@router.post("/curriculum/generate", response_model=CurriculumResponse)
async def generate_training_curriculum(
    req: GenerateCurriculumRequest,
):
    """
    Generates a targeted training curriculum with hands-on coding modules,
    test harnesses, and empirical benchmark evaluations.
    """
    try:
        generator = CurriculumGenerator()
        curriculum = generator.generate_curriculum(
            skill_name=req.skill_name,
            target_level=req.target_level,
            gap_reason=req.gap_reason,
        )
        return CurriculumResponse(**curriculum.to_dict())
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))


@router.post("/cycle", response_model=EvolutionCycleResponse)
async def trigger_evolution_cycle(
    req: TriggerEvolutionCycleRequest = TriggerEvolutionCycleRequest(),
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    project_repo: ProjectRepository = Depends(get_project_repo),
):
    """
    Triggers complete autonomous recursive self-evolution cycle:
    OBSERVE -> IDENTIFY -> HYPOTHESIZE -> CURRICULUM -> EXPERIMENT -> RECORD.
    """
    engine = SelfEvolutionEngine(
        evolution_repo=evolution_repo,
        employee_repo=employee_repo,
        task_repo=task_repo,
        project_repo=project_repo,
    )
    cycle_res = await engine.run_evolution_cycle(
        trigger_reason=req.trigger_reason,
        target_skills=req.target_skills,
    )
    return EvolutionCycleResponse(**cycle_res)


@router.get("/records", response_model=list[EvolutionRecordResponse])
async def list_evolution_records(
    record_type: str | None = Query(None, description="Filter by type (curriculum, prompt, tool, model)"),
    status: str | None = Query(None, description="Filter by status (completed, in_review, rolled_back)"),
    limit: int = Query(50, ge=1, le=100),
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """Lists historical evolution and experiment records."""
    records = await evolution_repo.list_all(
        record_type=record_type,
        status=status,
        limit=limit,
    )
    return [serialize_evolution_record(r) for r in records]


@router.get("/records/{record_id}", response_model=EvolutionRecordResponse)
async def get_evolution_record(
    record_id: str,
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """Retrieves specific evolution experiment record by ID."""
    record = await evolution_repo.get_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Evolution record '{record_id}' not found")
    return serialize_evolution_record(record)
