from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import get_project_repo, get_task_repo
from neural.api.schemas.responses import ProjectResponse, TaskResponse
from neural.db.repositories import ProjectRepository, TaskRepository

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.get("", response_model=list[ProjectResponse])
@router.get("/", response_model=list[ProjectResponse])
async def list_projects(project_repo: ProjectRepository = Depends(get_project_repo)):
    projects = await project_repo.list_all()
    return [
        ProjectResponse(
            id=p.id,
            name=p.name,
            objective=p.objective,
            status=p.status,
            execution_plan=p.execution_plan,
            created_at=p.created_at,
            updated_at=p.updated_at
        )
        for p in projects
    ]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, project_repo: ProjectRepository = Depends(get_project_repo)):
    project = await project_repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(
        id=project.id,
        name=project.name,
        objective=project.objective,
        status=project.status,
        execution_plan=project.execution_plan,
        created_at=project.created_at,
        updated_at=project.updated_at
    )


@router.get("/{project_id}/tasks", response_model=list[TaskResponse])
async def get_project_tasks(
    project_id: str,
    project_repo: ProjectRepository = Depends(get_project_repo),
    task_repo: TaskRepository = Depends(get_task_repo)
):
    project = await project_repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    tasks = await task_repo.list_by_project(project_id)
    return [
        TaskResponse(
            id=t.id,
            task_id=t.task_id,
            project_id=t.project_id,
            title=t.title,
            execution_mode=t.execution_mode,
            status=t.status,
            assigned_to=t.assigned_to,
            priority=t.priority.value if hasattr(t.priority, "value") else str(t.priority),
            scheduled_at=t.scheduled_at,
            started_at=t.started_at,
            completed_at=t.completed_at
        )
        for t in tasks
    ]


@router.get("/{project_id}/tasks/{task_id}", response_model=TaskResponse)
async def get_project_task_by_id(
    project_id: str,
    task_id: str,
    project_repo: ProjectRepository = Depends(get_project_repo),
    task_repo: TaskRepository = Depends(get_task_repo)
):
    project = await project_repo.get_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    task = await task_repo.get_by_task_id(task_id)
    if not task or task.project_id != project.id:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found in project")
    return TaskResponse(
        id=task.id,
        task_id=task.task_id,
        project_id=task.project_id,
        title=task.title,
        execution_mode=task.execution_mode,
        status=task.status,
        assigned_to=task.assigned_to,
        priority=task.priority.value if hasattr(task.priority, "value") else str(task.priority),
        scheduled_at=task.scheduled_at,
        started_at=task.started_at,
        completed_at=task.completed_at
    )


