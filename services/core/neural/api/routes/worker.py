from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import (
    get_audit_repo,
    get_container_manager,
    get_container_repo,
    get_employee_repo,
    get_task_repo,
    require_worker_token,
)
from neural.api.schemas.requests import (
    WorkerClaimRequest,
    WorkerCommandRequest,
    WorkerFailureRequest,
    WorkerLeaseRequest,
    WorkerSubmitRequest,
)
from neural.constants import EmployeeStatus, ExecutionMode
from neural.containers.manager import ContainerManager
from neural.db.repositories import (
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    TaskRepository,
)

router = APIRouter(
    prefix="/internal/worker",
    tags=["Worker protocol"],
    dependencies=[Depends(require_worker_token)],
)


@router.post("/tasks/claim")
async def claim_next_task(
    request: WorkerClaimRequest = WorkerClaimRequest(),
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
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
    task = await task_repo.get_by_task_id(task_id)
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
    started = await task_repo.start_task_attempt(task_id, worker_id, request.fencing_token)
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
    lease_expires_at = await task_repo.heartbeat_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
    )
    if lease_expires_at is None:
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    return {"task_id": task_id, "status": "running", "lease_expires_at": lease_expires_at}


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
