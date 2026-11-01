from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop
from neural.gui_intelligence.benchmarks.human_speed import HumanSpeedComparator
from neural.gui_intelligence.benchmarks.osworld_runner import OSWorldRunner
from neural.gui_intelligence.brain.task_planner import System2TaskPlanner
from neural.gui_intelligence.models import (
    ExperienceTrajectoryRecord,
    GUIActionReceipt,
    GUIActionRequest,
)
from neural.gui_intelligence.perception.screen_capture import (
    ScreenCaptureEngine,
    ScreenCaptureUnavailableError,
)
from neural.gui_intelligence.skills.skill_compiler import GUISkillCompiler
from neural.gui_intelligence.skills.skill_registry import get_gui_skill_registry
from neural.gui_intelligence.skills.skill_validator import SkillValidator
from pydantic import BaseModel

router = APIRouter(prefix="/gui", tags=["Neural-Speed GUI Intelligence"])


class PlanRequest(BaseModel):
    task_objective: str
    max_steps: int = 15


class ScreenCaptureResponse(BaseModel):
    timestamp: float
    resolution: tuple[int, int]
    frame_hash: str
    capture_latency_ms: float


class HumanSpeedRequest(BaseModel):
    task_id: str
    task_name: str
    human_time_seconds: float
    ancos_time_seconds: float
    passed_verification: bool
    safety_violation: bool = False


class OSWorldRunRequest(BaseModel):
    task_id: str
    task_name: str
    task_objective: str
    category: str = "daily_workflows"
    expected_deliverable_text: Optional[str] = None
    max_steps: int = 15


class CompileSkillRequest(BaseModel):
    trajectory: ExperienceTrajectoryRecord
    skill_name: Optional[str] = None
    description: Optional[str] = None


@router.post("/plan")
async def plan_task(req: PlanRequest) -> dict[str, Any]:
    """Generates a System 2 Deep Brain symbolic plan for a GUI task."""
    planner = System2TaskPlanner()
    plan = await planner.create_plan(req.task_objective)
    return plan.to_dict()


@router.post("/action", response_model=GUIActionReceipt)
async def execute_gui_action(req: GUIActionRequest) -> GUIActionReceipt:
    """Dispatches a single motor action through the observe-ground-act-verify loop."""
    loop = GUIExecutionLoop()
    try:
        return loop.execute_action(req)
    except ScreenCaptureUnavailableError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err


@router.post("/chunk", response_model=list[GUIActionReceipt])
async def execute_action_chunk(chunk: list[GUIActionRequest]) -> list[GUIActionReceipt]:
    """Dispatches a macro action chunk through the motor loop."""
    loop = GUIExecutionLoop()
    try:
        return loop.execute_chunk(chunk)
    except ScreenCaptureUnavailableError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err


@router.get("/screen/capture", response_model=ScreenCaptureResponse)
async def capture_screen() -> ScreenCaptureResponse:
    """Captures a fresh timestamped screen frame from the active desktop."""
    capture = ScreenCaptureEngine()
    try:
        frame = capture.capture_frame()
    except ScreenCaptureUnavailableError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    return ScreenCaptureResponse(
        timestamp=frame.timestamp,
        resolution=frame.resolution,
        frame_hash=frame.frame_hash,
        capture_latency_ms=frame.capture_latency_ms,
    )


@router.post("/benchmark/human-speed")
async def evaluate_human_speed(req: HumanSpeedRequest) -> dict[str, Any]:
    """Evaluates task speedup against human median baseline under the 4x objective."""
    res = HumanSpeedComparator.evaluate_speedup(
        task_id=req.task_id,
        task_name=req.task_name,
        human_time_seconds=req.human_time_seconds,
        ancos_time_seconds=req.ancos_time_seconds,
        passed_verification=req.passed_verification,
        safety_violation=req.safety_violation,
    )
    return res.to_dict()


@router.post("/benchmark/osworld")
async def run_osworld_benchmark(req: OSWorldRunRequest) -> dict[str, Any]:
    """Runs an OSWorld 2.1 benchmark task against the desktop environment."""
    runner = OSWorldRunner()
    res = await runner.run_task(
        task_id=req.task_id,
        task_name=req.task_name,
        task_objective=req.task_objective,
        category=req.category,
        expected_deliverable_text=req.expected_deliverable_text,
        max_steps=req.max_steps,
    )
    return res.to_dict()


@router.get("/skills")
async def list_gui_skills(
    application: Optional[str] = None,
    certified_only: bool = False,
) -> list[dict[str, Any]]:
    """Lists compiled GUI skills from the registry."""
    reg = get_gui_skill_registry()
    skills = reg.list_skills(application_name=application, only_certified=certified_only)
    return [s.to_dict() for s in skills]


@router.post("/skills/compile")
async def compile_gui_skill(req: CompileSkillRequest) -> dict[str, Any]:
    """Compiles a recorded experience trajectory into a reusable GUI skill."""
    compiler = GUISkillCompiler()
    skill = compiler.compile_trajectory(
        trajectory=req.trajectory,
        skill_name=req.skill_name,
        description=req.description,
    )
    reg = get_gui_skill_registry()
    reg.register_skill(skill)
    return skill.to_dict()


@router.post("/skills/{skill_id}/certify")
async def certify_gui_skill(skill_id: str) -> dict[str, Any]:
    """Runs the 3-permutation held-out generalization test suite to certify a skill."""
    reg = get_gui_skill_registry()
    skill = reg.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"GUI skill '{skill_id}' not found")
    validator = SkillValidator(registry=reg)
    res = validator.validate_skill(skill)
    return {
        "passed": res.passed,
        "skill_id": res.skill_id,
        "consecutive_passes": res.consecutive_passes,
        "required_passes": res.required_passes,
        "score": res.score,
        "certificate_hash": res.certificate_hash,
        "permutation_results": res.permutation_results,
    }
