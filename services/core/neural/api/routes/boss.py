from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import (
    get_audit_repo,
    get_employee_repo,
    get_project_repo,
    get_task_repo,
    require_owner_identity,
)
from neural.api.schemas.requests import ObjectiveSubmissionRequest
from neural.api.schemas.responses import ProjectResponse
from neural.boss.brain import BossBrain
from neural.db.repositories import (
    AuditRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)

router = APIRouter(prefix="/boss", tags=["Boss AI"])


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
):
    brain = BossBrain(project_repo=project_repo, task_repo=task_repo)
    return await brain.get_status()

