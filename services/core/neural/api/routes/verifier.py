from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_container_manager,
    get_container_repo,
    get_employee_repo,
    get_service_heartbeat_repo,
    get_task_repo,
    require_execution_not_halted,
    require_verifier_token,
)
from neural.api.schemas.requests import (
    RuntimeHeartbeatRequest,
    VerificationDecisionRequest,
    VerifierCommandRequest,
    VerifierManifestRequest,
)
from neural.constants import EmployeeStatus, ExecutionMode
from neural.containers.manager import ContainerManager
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    ServiceHeartbeatRepository,
    TaskRepository,
)
from neural.security.kill_switch import get_kill_switch

router = APIRouter(
    prefix="/internal/verifier",
    tags=["Independent verification"],
    dependencies=[Depends(require_verifier_token), Depends(require_execution_not_halted)],
)


@router.post("/runtime/heartbeat")
async def heartbeat_verifier_runtime(
    request: RuntimeHeartbeatRequest,
    heartbeat_repo: ServiceHeartbeatRepository = Depends(get_service_heartbeat_repo),
):
    receipt = await heartbeat_repo.record("verifier", "verification-service", request.instance_id)
    return {
        "service_type": receipt.service_type,
        "service_id": receipt.service_id,
        "instance_id": receipt.instance_id,
        "last_seen_at": receipt.last_seen_at,
    }


@router.get("/queue")
async def list_verification_queue(
    limit: int = Query(default=50, ge=1, le=200),
    task_repo: TaskRepository = Depends(get_task_repo),
):
    get_kill_switch().guard_active()
    return {"items": await task_repo.list_pending_verification(limit)}


@router.post("/tasks/{task_id}/attempts/{attempt_id}/exec")
async def execute_verifier_test(
    task_id: str,
    attempt_id: str,
    request: VerifierCommandRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.execution_mode != ExecutionMode.TERMINAL:
        raise HTTPException(status_code=409, detail="Automated verifier execution only supports terminal tasks.")
    if not await task_repo.verification_attempt_is_current(task_id, attempt_id, request.fencing_token):
        raise HTTPException(status_code=409, detail="Verification attempt is stale or no longer pending.")
    employee = await employee_repo.get_by_employee_id(task.assigned_to) if task.assigned_to else None
    if not employee:
        raise HTTPException(status_code=409, detail="Task has no assigned employee workspace.")
    workstation = await container_manager.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    if not workstation or workstation.get("status") != "running":
        raise HTTPException(status_code=409, detail="Assigned employee workstation is not live.")
    result = await container_manager.execute_verifier_command(employee.employee_id, request.command)
    return {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "fencing_token": request.fencing_token,
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout", ""))[-16000:],
        "stderr": str(result.get("stderr", ""))[-4000:],
    }


@router.post("/tasks/{task_id}/attempts/{attempt_id}/manifest")
async def capture_verifier_manifest(
    task_id: str,
    attempt_id: str,
    request: VerifierManifestRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.execution_mode != ExecutionMode.TERMINAL:
        raise HTTPException(status_code=409, detail="Automated verifier manifests only support terminal tasks.")
    if not await task_repo.verification_attempt_is_current(task_id, attempt_id, request.fencing_token):
        raise HTTPException(status_code=409, detail="Verification attempt is stale or no longer pending.")
    employee = await employee_repo.get_by_employee_id(task.assigned_to) if task.assigned_to else None
    if not employee:
        raise HTTPException(status_code=409, detail="Task has no assigned employee workspace.")
    workstation = await container_manager.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    if not workstation or workstation.get("status") != "running":
        raise HTTPException(status_code=409, detail="Assigned employee workstation is not live.")
    return await container_manager.capture_verifier_manifest(employee.employee_id)


@router.post("/tasks/{task_id}/attempts/{attempt_id}/review")
async def review_task_attempt(
    task_id: str,
    attempt_id: str,
    decision: VerificationDecisionRequest,
    verifier_id: str = Depends(require_verifier_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
):
    get_kill_switch().guard_active()
    receipt = decision.model_dump()
    outcome = await task_repo.review_task_attempt(task_id, attempt_id, verifier_id, receipt)
    if outcome is None:
        raise HTTPException(
            status_code=409,
            detail="The submitted attempt is stale, already reviewed, or no longer the task's active fenced attempt.",
        )
    if outcome["task_status"] in {"completed", "failed"}:
        task = await task_repo.get_by_task_id(task_id)
        if task and task.assigned_to:
            await employee_repo.update_status(task.assigned_to, EmployeeStatus.IDLE)
    return outcome
