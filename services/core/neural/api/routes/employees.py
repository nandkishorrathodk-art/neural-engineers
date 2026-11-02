from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_approval_repo,
    get_audit_repo,
    get_container_manager,
    get_container_repo,
    get_employee_repo,
    get_task_repo,
    require_owner_identity,
)
from neural.api.schemas.responses import (
    EmployeeResponse,
    TaskResponse,
    VncCredentialResponse,
    WorkstationResponse,
)
from neural.containers.manager import ContainerManager, DockerBrokerRequestError
from neural.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    TaskRepository,
)

router = APIRouter(prefix="/employees", tags=["Employees"])


def serialize_employee_model(emp, desktop_url: Optional[str] = None) -> EmployeeResponse:
    skills_data = [
        {"name": s.skill_name, "level": s.level.value if hasattr(s.level, "value") else str(s.level), "verified": s.verified}
        for s in emp.skills
    ]
    return EmployeeResponse(
        id=emp.id,
        employee_id=emp.employee_id,
        name=emp.name,
        title=emp.title,
        department=emp.department,
        status=emp.status,
        skills=skills_data,
        hired_at=emp.hired_at,
        container_id=emp.container_id,
        desktop_url=desktop_url
    )


@router.get("", response_model=list[EmployeeResponse])
@router.get("/", response_model=list[EmployeeResponse])
async def list_employees(
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_mgr: ContainerManager = Depends(get_container_manager)
):
    employees = await employee_repo.list_all()
    res = []
    for emp in employees:
        desktop_spec = await container_mgr.get_desktop_by_employee_id(
            emp.employee_id,
            container_repo=container_repo,
            employee_repo=employee_repo
        )
        desktop_url = desktop_spec.get("desktop_url") if desktop_spec else None
        res.append(serialize_employee_model(emp, desktop_url=desktop_url))
    return res


@router.post("", response_model=EmployeeResponse)
@router.post("/", response_model=EmployeeResponse)
async def create_employee():
    raise HTTPException(
        status_code=405,
        detail="Employees can only be created by Boss staffing decisions for an owner-submitted objective.",
    )


@router.get("/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: str,
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_mgr: ContainerManager = Depends(get_container_manager)
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    desktop_spec = await container_mgr.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo
    )
    desktop_url = desktop_spec.get("desktop_url") if desktop_spec else None
    return serialize_employee_model(employee, desktop_url=desktop_url)


@router.get("/{employee_id}/workstation", response_model=WorkstationResponse)
async def get_employee_workstation(
    employee_id: str,
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_mgr: ContainerManager = Depends(get_container_manager)
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    desktop_spec = await container_mgr.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo
    )
    if not desktop_spec:
        raise HTTPException(status_code=404, detail=f"No active workstation found for employee '{employee_id}'")

    return WorkstationResponse(
        docker_id=desktop_spec["docker_id"],
        employee_id=desktop_spec["employee_id"],
        image=desktop_spec["image"],
        vnc_port=desktop_spec["vnc_port"],
        novnc_port=desktop_spec["novnc_port"],
        desktop_url=desktop_spec["desktop_url"],
        cpu_limit=desktop_spec["cpu_limit"],
        mem_limit=desktop_spec["mem_limit"],
        status=desktop_spec["status"],
        workspace=desktop_spec["workspace"],
        started_at=desktop_spec.get("started_at")
    )


@router.post("/{employee_id}/workstation/provision", response_model=WorkstationResponse)
async def provision_employee_workstation(
    employee_id: str,
    approval_id: str = Query(..., min_length=1),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
    actor: str = Depends(require_owner_identity),
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    approval = await approval_repo.consume_once(
        approval_id,
        "PROVISION_WORKSTATION",
        {"employee_id": employee.employee_id},
        actor,
    )
    if not approval:
        raise HTTPException(status_code=409, detail="A current, approved, unused workstation-provision approval is required.")

    role_image = "employee-base"
    if employee.model_config_data and "container_template" in employee.model_config_data:
        role_image = employee.model_config_data["container_template"]

    try:
        spec = await container_mgr.provision_employee_desktop(
            employee_id=employee.employee_id,
            role_image=role_image,
            container_repo=container_repo,
            employee_repo=employee_repo
        )
    except DockerBrokerRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    await audit_repo.log(
        actor=actor,
        action="PROVISION_WORKSTATION",
        resource_type="Container",
        resource_id=spec["docker_id"],
        details={
            "employee_id": employee.employee_id,
            "vnc_port": spec["vnc_port"],
            "approval_id": approval.id,
        },
    )

    return WorkstationResponse(
        docker_id=spec["docker_id"],
        employee_id=spec["employee_id"],
        image=spec["image"],
        vnc_port=spec["vnc_port"],
        novnc_port=spec["novnc_port"],
        desktop_url=spec["desktop_url"],
        cpu_limit=spec["cpu_limit"],
        mem_limit=spec["mem_limit"],
        status=spec["status"],
        workspace=spec["workspace"],
        started_at=spec.get("started_at"),
        vnc_password=spec.get("vnc_password")
    )


@router.post("/{employee_id}/workstation/vnc-password", response_model=VncCredentialResponse)
async def rotate_employee_vnc_password(
    employee_id: str,
    approval_id: str = Query(..., min_length=1),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
    actor: str = Depends(require_owner_identity),
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    workstation = await container_mgr.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    if not workstation:
        raise HTTPException(status_code=404, detail=f"No workstation found for employee '{employee_id}'")
    if workstation.get("status") != "running":
        raise HTTPException(status_code=409, detail="Workstation must be running before rotating its VNC password")

    approval = await approval_repo.consume_once(
        approval_id,
        "ROTATE_VNC_PASSWORD",
        {"employee_id": employee.employee_id, "docker_id": workstation["docker_id"]},
        actor,
    )
    if not approval:
        raise HTTPException(status_code=409, detail="A current, approved, unused password-rotation approval is required.")

    password = await container_mgr.rotate_vnc_password(workstation["docker_id"])
    await audit_repo.log(
        actor=actor,
        action="ROTATE_VNC_PASSWORD",
        resource_type="Container",
        resource_id=workstation["docker_id"],
        details={"employee_id": employee.employee_id, "approval_id": approval.id},
    )
    return VncCredentialResponse(
        employee_id=employee.employee_id,
        docker_id=workstation["docker_id"],
        vnc_password=password,
    )


@router.post("/{employee_id}/workstation/terminate")
async def terminate_employee_workstation(
    employee_id: str,
    approval_id: str = Query(..., min_length=1),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
    actor: str = Depends(require_owner_identity),
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    desktop_spec = await container_mgr.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo
    )
    if not desktop_spec:
        raise HTTPException(status_code=404, detail=f"No active workstation found for employee '{employee_id}'")

    docker_id = desktop_spec["docker_id"]
    approval = await approval_repo.consume_once(
        approval_id,
        "TERMINATE_WORKSTATION",
        {"employee_id": employee.employee_id, "docker_id": docker_id},
        actor,
    )
    if not approval:
        raise HTTPException(status_code=409, detail="A current, approved, unused workstation-termination approval is required.")
    await container_mgr.terminate_desktop(docker_id, container_repo=container_repo)

    await audit_repo.log(
        actor=actor,
        action="TERMINATE_WORKSTATION",
        resource_type="Container",
        resource_id=docker_id,
        details={"employee_id": employee.employee_id, "approval_id": approval.id}
    )

    return {"status": "terminated", "docker_id": docker_id, "employee_id": employee.employee_id}


@router.get("/{employee_id}/tasks", response_model=list[TaskResponse])
async def get_employee_tasks(
    employee_id: str,
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    task_repo: TaskRepository = Depends(get_task_repo)
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    tasks = await task_repo.list_by_assignee(employee.employee_id)
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
            started_at=t.started_at,
            completed_at=t.completed_at
        )
        for t in tasks
    ]


