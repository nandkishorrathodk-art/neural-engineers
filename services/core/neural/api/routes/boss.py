from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import (
    get_approval_repo,
    get_audit_repo,
    get_container_manager,
    get_employee_repo,
    get_project_repo,
    get_resource_lease_repo,
    get_service_heartbeat_repo,
    get_task_repo,
    require_owner_identity,
)
from neural.api.schemas.requests import (
    ObjectiveSubmissionRequest,
    TaskRecoveryApprovalRequest,
    TaskRecoveryRequest,
)
from neural.api.schemas.responses import BossProjectReport, ProjectResponse
from neural.boss.brain import BossBrain
from neural.constants import TaskStatus
from neural.containers.manager import ContainerManager, DockerBrokerRequestError
from neural.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    EmployeeRepository,
    ProjectRepository,
    ResourceLeaseRepository,
    ServiceHeartbeatRepository,
    TaskRepository,
)
from neural.exceptions import ContainerError
from neural.security.approval_gates import HumanApprovalGate
from neural.security.kill_switch import get_kill_switch

router = APIRouter(prefix="/boss", tags=["Boss AI"])


async def _require_unattempted_orphan(
    task_id: str,
    task_repo: TaskRepository,
    employee_repo: EmployeeRepository,
    resource_lease_repo: ResourceLeaseRepository,
    container_mgr: ContainerManager,
):
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    if task.status != TaskStatus.RUNNING or not task.assigned_to:
        raise HTTPException(status_code=409, detail="Only an assigned RUNNING task can be assessed for orphan recovery.")
    attempts = await task_repo.list_attempts_for_task(task.task_id)
    if attempts:
        raise HTTPException(status_code=409, detail="Task has attempt history; this recovery path only handles tasks with no attempts.")

    employee = await employee_repo.get_by_employee_id(task.assigned_to)
    if not employee:
        raise HTTPException(status_code=409, detail="Assigned employee is missing; manual review is required.")
    docker_id = f"ancos-desktop-{employee.employee_id.lower()}"
    leases = await resource_lease_repo.list_blocking_for_employee(
        employee_id=employee.employee_id,
        internal_employee_id=employee.id,
        resource_key=docker_id,
    )
    if leases:
        raise HTTPException(status_code=409, detail="An active or quarantined workstation lease blocks task recovery.")

    try:
        await container_mgr._broker_request("GET", f"/v1/containers/{docker_id}")
    except DockerBrokerRequestError as exc:
        if exc.status_code != 404:
            raise HTTPException(status_code=503, detail="Broker could not confirm workstation absence.") from exc
    except ContainerError as exc:
        raise HTTPException(status_code=503, detail="Broker could not confirm workstation absence.") from exc
    else:
        raise HTTPException(status_code=409, detail="A managed desktop still exists; terminate it only through its approved lifecycle.")

    return task, employee


@router.post("/objective", response_model=ProjectResponse)
async def submit_objective(
    payload: ObjectiveSubmissionRequest,
    project_repo: ProjectRepository = Depends(get_project_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
    owner_identity: str = Depends(require_owner_identity),
):
    if not payload.objective or not payload.objective.strip():
        raise HTTPException(status_code=400, detail="Objective cannot be empty")

    employees_before = await employee_repo.list_all()
    existing_ids = {employee.employee_id for employee in employees_before}
    brain = BossBrain(project_repo=project_repo, task_repo=task_repo, employee_repo=employee_repo)
    # Strategic analysis, planning, project & tasks persistence, and task assignment
    project, tasks = await brain.plan_and_create_project(
        objective=payload.objective,
        project_name=payload.project_name,
        auto_assign_first_task=True,
        auto_recruit_workforce=True,
    )

    employees_after = await employee_repo.list_all()
    recruited_ids = sorted(
        employee.employee_id
        for employee in employees_after
        if employee.employee_id not in existing_ids
    )

    await audit_repo.log(
        actor=owner_identity,
        action="SUBMIT_OBJECTIVE",
        resource_type="Project",
        resource_id=project.id,
        details={
            "name": project.name,
            "objective": payload.objective,
            "tasks_count": len(tasks),
            "plan_source": (project.execution_plan or {}).get("plan_source", "unknown"),
        }
    )
    await audit_repo.log(
        actor="BossAI",
        action="STAFFING_DECISION",
        resource_type="Project",
        resource_id=project.id,
        details={
            "recruitment_trigger": (
                "verified_skill_or_available_capacity_gap"
                if recruited_ids
                else "existing_verified_capabilities_adequate"
            ),
            "recruited_employee_ids": recruited_ids,
            "assigned_task_ids": [task.task_id for task in tasks if task.assigned_to],
            "plan_source": (project.execution_plan or {}).get("plan_source", "unknown"),
        },
    )

    return ProjectResponse(
        id=project.id,
        name=project.name,
        objective=project.objective,
        status=project.status,
        execution_plan=project.execution_plan,
        created_at=project.created_at,
        updated_at=project.updated_at
    )



@router.get("/status")
async def get_boss_status(
    project_repo: ProjectRepository = Depends(get_project_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    service_heartbeat_repo: ServiceHeartbeatRepository = Depends(get_service_heartbeat_repo),
):
    brain = BossBrain(
        project_repo=project_repo,
        task_repo=task_repo,
        service_heartbeat_repo=service_heartbeat_repo,
    )
    return await brain.get_status()


@router.get("/projects/{project_id}/report", response_model=BossProjectReport)
async def get_project_report(
    project_id: str,
    project_repo: ProjectRepository = Depends(get_project_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    _owner: str = Depends(require_owner_identity),
):
    brain = BossBrain(
        project_repo=project_repo,
        task_repo=task_repo,
        employee_repo=employee_repo,
    )
    report = await brain.build_project_report(project_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return report


@router.post("/tasks/{task_id}/recovery-approvals")
async def request_orphan_task_recovery_approval(
    task_id: str,
    payload: TaskRecoveryApprovalRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    resource_lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
    actor: str = Depends(require_owner_identity),
) -> dict[str, object]:
    get_kill_switch().guard_active()
    task, employee = await _require_unattempted_orphan(
        task_id, task_repo, employee_repo, resource_lease_repo, container_mgr
    )
    details = {
        "task_id": task.task_id,
        "project_id": task.project_id,
        "expected_fencing_token": task.fencing_token,
        "employee_id": employee.employee_id,
    }
    approval = await HumanApprovalGate.request_approval(
        action_type="RECOVER_ORPHANED_TASK",
        requested_by=actor,
        description=f"Requeue unattempted RUNNING task {task.task_id}",
        justification=payload.justification,
        details=details,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
    )
    return {"id": approval.id, "action_type": approval.action_type, "status": approval.status.value, "details": approval.details}


@router.post("/tasks/{task_id}/recover")
async def recover_orphaned_task(
    task_id: str,
    payload: TaskRecoveryRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    resource_lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
    actor: str = Depends(require_owner_identity),
    container_mgr: ContainerManager = Depends(get_container_manager),
) -> dict[str, object]:
    get_kill_switch().guard_active()
    task, employee = await _require_unattempted_orphan(
        task_id, task_repo, employee_repo, resource_lease_repo, container_mgr
    )
    result = await task_repo.recover_unattempted_running_task(
        task_id=task.task_id,
        expected_fencing_token=task.fencing_token,
        expected_employee_id=employee.employee_id,
        approval_id=payload.approval_id,
        actor=actor,
    )
    if result is None:
        raise HTTPException(
            status_code=409,
            detail="Recovery conditions changed or the independent approval is missing, mismatched, or already consumed.",
        )
    return result

