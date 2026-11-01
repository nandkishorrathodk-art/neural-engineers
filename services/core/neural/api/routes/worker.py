from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import (
    get_container_manager,
    get_container_repo,
    get_employee_repo,
    get_task_repo,
    require_worker_token,
)
from neural.api.schemas.requests import (
    WorkerClaimRequest,
    WorkerFailureRequest,
    WorkerLeaseRequest,
    WorkerSubmitRequest,
)
from neural.constants import EmployeeStatus
from neural.containers.manager import ContainerManager
from neural.db.repositories import ContainerRepository, EmployeeRepository, TaskRepository

router = APIRouter(
    prefix="/internal/worker",
    tags=["Worker protocol"],
    dependencies=[Depends(require_worker_token)],
)


@router.post("/tasks/claim")
async def claim_next_task(
    request: WorkerClaimRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
    task = await task_repo.claim_next_task(request.worker_id)
    if task:
        workstation = await container_manager.get_desktop_by_employee_id(
            task["employee_id"],
            container_repo=container_repo,
            employee_repo=employee_repo,
        )
        if not workstation or workstation.get("status") != "running":
            await task_repo.release_task_claim(
                task["task_id"],
                request.worker_id,
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
            request.worker_id,
            request.fencing_token,
            "Assigned employee workstation is not live at worker start.",
        )
        raise HTTPException(status_code=409, detail="Assigned employee workstation is not live.")
    started = await task_repo.start_task_attempt(task_id, request.worker_id, request.fencing_token)
    if not started:
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    return {"task_id": task_id, "status": "running", "fencing_token": request.fencing_token}


@router.post("/tasks/{task_id}/heartbeat")
async def heartbeat_task_attempt(
    task_id: str,
    request: WorkerLeaseRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
):
    lease_expires_at = await task_repo.heartbeat_task_attempt(
        task_id,
        request.worker_id,
        request.fencing_token,
    )
    if lease_expires_at is None:
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    return {"task_id": task_id, "status": "running", "lease_expires_at": lease_expires_at}


@router.post("/tasks/{task_id}/submit")
async def submit_task_attempt(
    task_id: str,
    request: WorkerSubmitRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
):
    submitted = await task_repo.submit_task_attempt(
        task_id,
        request.worker_id,
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
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
):
    task = await task_repo.get_by_task_id(task_id)
    failed = await task_repo.fail_task_attempt(
        task_id,
        request.worker_id,
        request.fencing_token,
        request.error_message,
    )
    if not failed:
        raise HTTPException(status_code=409, detail="Worker lease expired or fencing token was superseded.")
    if task and task.assigned_to:
        await employee_repo.update_status(task.assigned_to, EmployeeStatus.IDLE)
    return {"task_id": task_id, "status": "failed", "fencing_token": request.fencing_token}
