from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import get_employee_repo, get_message_bus, get_project_repo, get_task_repo
from neural.api.schemas.requests import (
    AssignTaskRequest,
    ExecuteDagRequest,
    UpdateTaskStatusRequest,
)
from neural.api.schemas.responses import ExecuteDagResponse, TaskDAGResponse, TaskResponse
from neural.config import get_settings
from neural.constants import EmployeeStatus, TaskStatus
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository
from neural.exceptions import (
    InvalidTaskStateTransitionError,
    TaskDependencyError,
    UnverifiedTaskError,
)
from neural.tasks.scheduler import TaskScheduler
from neural.tasks.state_machine import TaskStateMachine

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("", response_model=list[TaskResponse])
@router.get("/", response_model=list[TaskResponse])
async def list_tasks(
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
    assigned_to: Optional[str] = Query(None, description="Filter by assigned employee ID"),
    task_repo: TaskRepository = Depends(get_task_repo)
):
    if project_id:
        tasks = await task_repo.list_by_project(project_id)
    else:
        tasks = await task_repo.list_all()

    filtered = []
    for t in tasks:
        if status and str(t.status).lower() != status.lower():
            continue
        if assigned_to and t.assigned_to != assigned_to:
            continue
        filtered.append(
            TaskResponse(
                id=t.id,
                task_id=t.task_id,
                project_id=t.project_id,
                title=t.title,
                execution_mode=t.execution_mode,
                status=t.status,
                assigned_to=t.assigned_to,
                priority=t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                started_at=t.started_at,
                completed_at=t.completed_at
            )
        )
    return filtered


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    task_repo: TaskRepository = Depends(get_task_repo)
):
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    return TaskResponse(
        id=task.id,
        task_id=task.task_id,
        project_id=task.project_id,
        title=task.title,
        execution_mode=task.execution_mode,
        status=task.status,
        assigned_to=task.assigned_to,
        priority=task.priority.value if hasattr(task.priority, "value") else str(task.priority),
        started_at=task.started_at,
        completed_at=task.completed_at
    )


@router.post("/{task_id}/assign", response_model=TaskResponse)
async def assign_task(
    task_id: str,
    request: AssignTaskRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    bus = Depends(get_message_bus)
):
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    if task.status not in (TaskStatus.PENDING, TaskStatus.ASSIGNED):
        raise HTTPException(
            status_code=409,
            detail=f"Task '{task_id}' cannot be assigned while it is {task.status.value}.",
        )

    employee = await employee_repo.get_by_employee_id(request.employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{request.employee_id}' not found")

    if employee.status in (EmployeeStatus.SUSPENDED, EmployeeStatus.TERMINATED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot assign task to employee '{request.employee_id}' with status '{employee.status.value}'"
        )

    already_assigned = task.assigned_to == employee.id
    if task.assigned_to and not already_assigned:
        previous_assignee = await employee_repo.get_by_employee_id(task.assigned_to)
        if previous_assignee and previous_assignee.status != EmployeeStatus.IDLE:
            raise HTTPException(
                status_code=409,
                detail="Task's current assignee is still reserved; release or expire its worker lease before reassignment.",
            )
    if employee.status != EmployeeStatus.IDLE and not (already_assigned and employee.status == EmployeeStatus.WORKING):
        raise HTTPException(
            status_code=409,
            detail=f"Employee '{request.employee_id}' is not available for another task.",
        )

    if already_assigned and task.status == TaskStatus.ASSIGNED and employee.status == EmployeeStatus.WORKING:
        return TaskResponse(
            id=task.id,
            task_id=task.task_id,
            project_id=task.project_id,
            title=task.title,
            execution_mode=task.execution_mode,
            status=task.status,
            assigned_to=task.assigned_to,
            priority=task.priority.value if hasattr(task.priority, "value") else str(task.priority),
            started_at=task.started_at,
            completed_at=task.completed_at,
        )

    await task_repo.assign_task(task.task_id, employee.id)
    # Assignment is a persisted reservation; execution begins only after a
    # worker acknowledges and supplies an execution receipt.
    await employee_repo.update_status(employee.employee_id, EmployeeStatus.WORKING)

    if bus:
        try:
            await bus.publish_task_event(
                task_id=task.task_id,
                event_type="task_assigned",
                status=TaskStatus.ASSIGNED.value,
                details={"assigned_to": employee.employee_id, "title": task.title}
            )
            await bus.send_to_employee(
                employee_id=employee.employee_id,
                message={
                    "message_type": "task_assignment",
                    "task_id": task.task_id,
                    "title": task.title,
                    "objective": task.objective
                }
            )
        except Exception as err:
            raise HTTPException(
                status_code=503,
                detail="Task is assigned, but its worker notification could not be delivered. Retry delivery before execution.",
            ) from err

    updated = await task_repo.get_by_task_id(task_id)
    return TaskResponse(
        id=updated.id,
        task_id=updated.task_id,
        project_id=updated.project_id,
        title=updated.title,
        execution_mode=updated.execution_mode,
        status=updated.status,
        assigned_to=updated.assigned_to,
        priority=updated.priority.value if hasattr(updated.priority, "value") else str(updated.priority),
        started_at=updated.started_at,
        completed_at=updated.completed_at
    )


@router.post("/{task_id}/status", response_model=TaskResponse)
async def update_task_status(
    task_id: str,
    request: UpdateTaskStatusRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    bus = Depends(get_message_bus)
):
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    try:
        target_status = TaskStateMachine._coerce_status(request.status)
        TaskStateMachine.validate_transition(task.status, target_status)
    except (InvalidTaskStateTransitionError, ValueError) as err:
        raise HTTPException(status_code=400, detail=str(err))

    if target_status == TaskStatus.RUNNING and get_settings().APP_ENV == "production":
        raise HTTPException(
            status_code=409,
            detail="A production task can enter running only after an authenticated worker claim; worker identity and leases are not configured.",
        )

    try:
        await task_repo.update_status(task.task_id, target_status)
    except UnverifiedTaskError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err

    if target_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED) and task.assigned_to:
        try:
            await employee_repo.update_status(task.assigned_to, EmployeeStatus.IDLE)
        except Exception:
            pass

    if bus:
        try:
            await bus.publish_task_event(
                task_id=task.task_id,
                event_type="status_changed",
                status=target_status.value,
                details={"notes": request.notes}
            )
        except Exception:
            pass

    updated = await task_repo.get_by_task_id(task_id)
    return TaskResponse(
        id=updated.id,
        task_id=updated.task_id,
        project_id=updated.project_id,
        title=updated.title,
        execution_mode=updated.execution_mode,
        status=updated.status,
        assigned_to=updated.assigned_to,
        priority=updated.priority.value if hasattr(updated.priority, "value") else str(updated.priority),
        started_at=updated.started_at,
        completed_at=updated.completed_at
    )


@router.get("/{task_id}/dependencies")
async def get_task_dependencies(
    task_id: str,
    task_repo: TaskRepository = Depends(get_task_repo)
):
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")

    scheduler = TaskScheduler(task_repo=task_repo)
    try:
        graph = await scheduler.build_graph_for_project(task.project_id)
        prereqs = graph.get_dependencies(task.task_id)
        successors = graph.get_dependents(task.task_id)
    except Exception:
        dep_objs = await task_repo.list_dependencies_for_task(task.task_id)
        prereqs = [d.depends_on_task_id for d in dep_objs]
        successors = []

    return {
        "task_id": task.task_id,
        "prerequisites": prereqs,
        "dependents": successors
    }


@router.get("/projects/{project_id}/dag", response_model=TaskDAGResponse)
async def get_project_dag(
    project_id: str,
    project_repo: ProjectRepository = Depends(get_project_repo),
    task_repo: TaskRepository = Depends(get_task_repo)
):
    project = await project_repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    scheduler = TaskScheduler(task_repo=task_repo)
    try:
        graph = await scheduler.build_graph_for_project(project.id)
        topological = graph.topological_sort()
        crit_path = graph.critical_path()
        ready = await scheduler.get_ready_tasks(project.id)

        nodes = [
            {"task_id": node, **graph.get_task_data(node)}
            for node in graph.graph.nodes
        ]
        edges = list(graph.graph.edges)

        return TaskDAGResponse(
            project_id=project.id,
            nodes=nodes,
            edges=edges,
            topological_order=topological,
            critical_path=crit_path,
            ready_tasks=[t.task_id for t in ready]
        )
    except TaskDependencyError as err:
        raise HTTPException(status_code=400, detail=f"DAG validation error: {err}")


@router.post("/projects/{project_id}/execute", response_model=ExecuteDagResponse)
async def execute_project_dag(
    project_id: str,
    request: ExecuteDagRequest = ExecuteDagRequest(),
    project_repo: ProjectRepository = Depends(get_project_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    bus = Depends(get_message_bus)
):
    project = await project_repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=employee_repo,
        max_concurrency=request.max_concurrency or 5,
        message_bus=bus
    )
    try:
        result = await scheduler.execute_dag(project.id)
    except TaskDependencyError as err:
        raise HTTPException(status_code=400, detail=f"Cannot execute DAG: {err}")
    except RuntimeError as err:
        raise HTTPException(status_code=503, detail=str(err))

    return ExecuteDagResponse(
        project_id=project.id,
        total_tasks=result.get("total_tasks", 0),
        completed=result.get("completed", []),
        failed=result.get("failed", []),
        blocked=result.get("blocked", []),
        success=result.get("success", False)
    )
