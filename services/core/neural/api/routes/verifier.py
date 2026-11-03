from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import get_employee_repo, get_task_repo, require_verifier_token
from neural.api.schemas.requests import VerificationDecisionRequest
from neural.constants import EmployeeStatus
from neural.db.repositories import EmployeeRepository, TaskRepository

router = APIRouter(
    prefix="/internal/verifier",
    tags=["Independent verification"],
    dependencies=[Depends(require_verifier_token)],
)


@router.get("/queue")
async def list_verification_queue(
    limit: int = Query(default=50, ge=1, le=200),
    task_repo: TaskRepository = Depends(get_task_repo),
):
    return {"items": await task_repo.list_pending_verification(limit)}


@router.post("/tasks/{task_id}/attempts/{attempt_id}/review")
async def review_task_attempt(
    task_id: str,
    attempt_id: str,
    decision: VerificationDecisionRequest,
    verifier_id: str = Depends(require_verifier_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
):
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
