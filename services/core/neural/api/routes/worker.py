from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import (
    get_audit_repo,
    get_container_manager,
    get_container_repo,
    get_employee_repo,
    get_llm_gateway,
    get_service_heartbeat_repo,
    get_task_repo,
    require_execution_not_halted,
    require_worker_token,
)
from neural.api.schemas.requests import (
    RuntimeHeartbeatRequest,
    WorkerClaimRequest,
    WorkerCommandRequest,
    WorkerFailureRequest,
    WorkerLeaseRequest,
    WorkerSubmitRequest,
)
from neural.constants import EmployeeStatus, ExecutionMode
from neural.containers.manager import ContainerManager
from neural.db.engine import get_db_session
from neural.db.models import Project
from neural.db.repositories import (
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    ServiceHeartbeatRepository,
    TaskRepository,
)
from neural.exceptions import ContainerError, ModelProviderError, QuotaExceededError
from neural.llm.gateway import LLMGateway
from neural.security.kill_switch import get_kill_switch
from neural.verification.test_commands import validate_test_plan
from neural.verification.workspace_manifest import valid_workspace_manifest
from neural.workers.terminal import TerminalCommandPlan
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(
    prefix="/internal/worker",
    tags=["Worker protocol"],
    dependencies=[Depends(require_worker_token), Depends(require_execution_not_halted)],
)


@router.post("/runtime/heartbeat")
async def heartbeat_worker_runtime(
    request: RuntimeHeartbeatRequest,
    worker_id: str = Depends(require_worker_token),
    heartbeat_repo: ServiceHeartbeatRepository = Depends(get_service_heartbeat_repo),
):
    receipt = await heartbeat_repo.record("worker", worker_id, request.instance_id)
    return {
        "service_type": receipt.service_type,
        "service_id": receipt.service_id,
        "instance_id": receipt.instance_id,
        "last_seen_at": receipt.last_seen_at,
    }


@router.post("/tasks/claim")
async def claim_next_task(
    request: WorkerClaimRequest = WorkerClaimRequest(),
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
    get_kill_switch().guard_active()
    task = await task_repo.claim_next_task(worker_id, execution_modes=request.execution_modes)
    if task:
        workstation = await container_manager.get_desktop_by_employee_id(
            task["employee_id"],
            container_repo=container_repo,
            employee_repo=employee_repo,
        )
        if not workstation or workstation.get("status") != "running":
            await task_repo.release_task_claim(
                task["task_id"],
                worker_id,
                task["fencing_token"],
                "Assigned employee workstation is not live and available.",
            )
            return {"task": None, "status": "workstation_unavailable"}
    return {"task": task, "status": "claimed" if task else "idle"}


@router.post("/tasks/{task_id}/start")
async def start_task_attempt(
    task_id: str,
    request: WorkerLeaseRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
    worker_id: str = Depends(require_worker_token),
):
    get_kill_switch().guard_active()
    if not await task_repo.worker_claim_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    task = await task_repo.get_by_task_id(task_id)
    if task and task.execution_mode != ExecutionMode.TERMINAL:
        await task_repo.release_task_claim(
            task_id,
            worker_id,
            request.fencing_token,
            f"Terminal worker cannot execute {task.execution_mode.value}-mode tasks.",
        )
        raise HTTPException(
            status_code=409,
            detail=f"Terminal worker cannot execute {task.execution_mode.value}-mode tasks.",
        )
    employee = await employee_repo.get_by_employee_id(task.assigned_to) if task and task.assigned_to else None
    workstation = (
        await container_manager.get_desktop_by_employee_id(
            employee.employee_id,
            container_repo=container_repo,
            employee_repo=employee_repo,
        )
        if employee
        else None
    )
    if not workstation or workstation.get("status") != "running":
        await task_repo.release_task_claim(
            task_id,
            worker_id,
            request.fencing_token,
            "Assigned employee workstation is not live at worker start.",
        )
        raise HTTPException(status_code=409, detail="Assigned employee workstation is not live.")
    try:
        workspace_manifest_before = await container_manager.capture_verifier_manifest(employee.employee_id)
    except ContainerError as exc:
        await task_repo.release_task_claim(
            task_id,
            worker_id,
            request.fencing_token,
            "Could not capture a complete task-start workspace baseline.",
        )
        raise HTTPException(status_code=503, detail="Task-start workspace baseline capture failed.") from exc
    if not valid_workspace_manifest(workspace_manifest_before, allow_empty=True):
        await task_repo.release_task_claim(
            task_id,
            worker_id,
            request.fencing_token,
            "Task-start workspace baseline was incomplete or invalid.",
        )
        raise HTTPException(status_code=503, detail="Task-start workspace baseline is incomplete or invalid.")

    started = await task_repo.start_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
        workspace_manifest_before,
    )
    if not started:
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    return {"task_id": task_id, "status": "running", "fencing_token": request.fencing_token}


@router.post("/tasks/{task_id}/heartbeat")
async def heartbeat_task_attempt(
    task_id: str,
    request: WorkerLeaseRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    worker_id: str = Depends(require_worker_token),
):
    get_kill_switch().guard_active()
    lease_expires_at = await task_repo.heartbeat_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
    )
    if lease_expires_at is None:
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    return {"task_id": task_id, "status": "running", "lease_expires_at": lease_expires_at}


@router.post("/tasks/{task_id}/plan")
async def plan_terminal_task(
    task_id: str,
    request: WorkerLeaseRequest,
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    gateway: LLMGateway = Depends(get_llm_gateway),
    session: AsyncSession = Depends(get_db_session),
):
    """Generate a terminal plan inside Core API so workers hold no provider credentials."""
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.execution_mode != ExecutionMode.TERMINAL:
        raise HTTPException(status_code=409, detail="This endpoint accepts terminal-mode tasks only.")
    if not await task_repo.worker_lease_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")

    resource_budget = task.resource_budget or {}
    raw_max_tokens = resource_budget.get("max_tokens", 6000)
    if isinstance(raw_max_tokens, bool) or not isinstance(raw_max_tokens, int) or raw_max_tokens < 1:
        raise HTTPException(status_code=409, detail="Task token budget is invalid.")
    max_tokens = min(6000, raw_max_tokens)
    project = await session.get(Project, task.project_id)
    if project is None:
        raise HTTPException(status_code=409, detail="Task project is missing; metered inference is blocked.")
    project_plan = project.execution_plan if isinstance(project.execution_plan, dict) else {}
    project_cap = project_plan.get("budget_usd", gateway.cost_tracker.max_project_budget_usd)
    task_cap = resource_budget.get("max_cost_usd", gateway.cost_tracker.max_task_budget_usd)
    month_scope = datetime.now(timezone.utc).strftime("%Y-%m")
    budget_scope_caps = {
        f"organization-month:{month_scope}": gateway.cost_tracker.monthly_hard_cap_usd,
        f"project:{project.id}": project_cap,
        f"task:{task.task_id}": task_cap,
    }
    try:
        plan, response = await gateway.generate_structured(
            prompt=(
                "Produce a short, bounded sequence of terminal argv commands to complete this task "
                "inside the employee workspace. Commands run directly without a shell in a disposable, "
                "non-root employee container. Do not access secrets, the host, Docker, or external services. "
                "Do not use destructive commands. Use only python, python3, pytest, npm, or npx. "
                "Persist all source and test deliverables as files in the workspace; never implement or test "
                "the deliverable only inside `python -c`. If acceptance requires tests to pass, include a "
                "replayable test command such as `python3 -m unittest -v`, `pytest -q`, or `npm test` that "
                "reads the persisted workspace files. Do not inline test definitions into a command. Commands "
                "must not claim success unless their exit codes are zero.\n\n"
                f"Title: {task.title}\nObjective: {task.objective}\nScope: {task.scope}\n"
                f"Acceptance criteria: {task.acceptance_criteria}"
            ),
            response_model=TerminalCommandPlan,
            route="code_generation",
            system_prompt="You are a terminal-mode software engineering employee. Return only executable argv arrays.",
            employee_id=task.assigned_to,
            task_id=task.task_id,
            project_id=task.project_id,
            temperature=0.1,
            max_tokens=max_tokens,
            db_session=session,
            budget_scope_caps=budget_scope_caps,
        )
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail="Budget reservation denied; provider was not called.") from exc
    except ModelProviderError as exc:
        raise HTTPException(status_code=502, detail="All configured planning providers failed.") from exc

    try:
        validate_test_plan(task.acceptance_criteria, plan.commands)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "plan": plan.model_dump(),
        "model": {
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
        },
    }


@router.post("/tasks/{task_id}/exec")
async def execute_terminal_command(
    task_id: str,
    request: WorkerCommandRequest,
    worker_id: str = Depends(require_worker_token),
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
        raise HTTPException(status_code=409, detail="This worker execution endpoint accepts terminal-mode tasks only.")
    if not await task_repo.worker_lease_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
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
    result = await container_manager.execute_command(
        workstation["docker_id"], request.command, workdir="/home/employee/workspace"
    )
    return {
        "task_id": task_id,
        "fencing_token": request.fencing_token,
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout", ""))[-16000:],
        "stderr": str(result.get("stderr", ""))[-4000:],
    }


@router.post("/tasks/{task_id}/submit")
async def submit_task_attempt(
    task_id: str,
    request: WorkerSubmitRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    worker_id: str = Depends(require_worker_token),
):
    get_kill_switch().guard_active()
    submitted = await task_repo.submit_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
        request.execution_receipt,
    )
    if not submitted:
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    return {
        "task_id": task_id,
        "status": "submitted_for_verification",
        "task_status": "running",
        "fencing_token": request.fencing_token,
    }


@router.post("/tasks/{task_id}/fail")
async def fail_task_attempt(
    task_id: str,
    request: WorkerFailureRequest,
    audit_repo: AuditRepository = Depends(get_audit_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    worker_id: str = Depends(require_worker_token),
):
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    failure = await task_repo.fail_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
        request.error_message,
    )
    if failure is None:
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    if task and task.assigned_to and not failure["retrying"]:
        await employee_repo.update_status(task.assigned_to, EmployeeStatus.IDLE)
    await audit_repo.log(
        actor="BossAI",
        action="TASK_FAILURE_RECOVERY",
        resource_type="Task",
        resource_id=task_id,
        details={
            "retrying": failure["retrying"],
            "retry_count": failure["retry_count"],
            "retry_after_seconds": failure["retry_after_seconds"],
            "reassigned": failure["reassigned"],
            "previous_employee_id": failure["previous_employee_id"],
            "assigned_employee_id": failure["assigned_employee_id"],
        },
    )
    return {
        "task_id": task_id,
        "status": "retrying" if failure["retrying"] else "failed",
        "retry_count": failure["retry_count"],
        "retry_after_seconds": failure["retry_after_seconds"],
        "fencing_token": request.fencing_token,
        "reassigned": failure["reassigned"],
        "assigned_employee_id": failure["assigned_employee_id"],
    }
