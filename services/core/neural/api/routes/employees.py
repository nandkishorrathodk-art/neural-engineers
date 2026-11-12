from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_approval_repo,
    get_audit_repo,
    get_container_manager,
    get_container_repo,
    get_employee_repo,
    get_resource_lease_repo,
    get_task_repo,
    require_owner_identity,
)
from neural.api.schemas.requests import AssignDeskRequest, PromoteEmployeeRequest
from neural.api.schemas.responses import (
    EmployeePromotionEligibilityResponse,
    EmployeeResponse,
    TaskResponse,
    VncCredentialResponse,
    VncTokenResponse,
    WorkstationAssignmentResponse,
    WorkstationResponse,
)
from neural.containers.manager import ContainerManager, DockerBrokerRequestError
from neural.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    ResourceLeaseRepository,
    TaskRepository,
)
from neural.employees.promotion import EmployeePromotionManager
from neural.resource_scheduler.workstation_lease import (
    claim_workstation_lease,
    release_workstation_lease,
    renew_workstation_lease,
)
from neural.workstations.assignment import WorkstationAssignmentService
from neural.workstations.layout import CANONICAL_WORKSTATIONS

router = APIRouter(prefix="/employees", tags=["Employees"])


async def _issue_workstation_vnc_token(
    container_mgr: ContainerManager,
    docker_id: str,
    *,
    rotate: bool = False,
) -> dict:
    """Mint a workstation's noVNC token, preferring the broker that owns it.

    The broker holds both the token file the gateway validates against and the
    Docker connection needed to repair the gateway bridge. Tests inject a fake
    container manager without that method, so fall back to the in-process token
    helpers when it is absent.
    """
    issue = getattr(container_mgr, "get_or_issue_vnc_token", None)
    if issue is not None:
        return await issue(docker_id, rotate=rotate)

    from neural.containers.broker_api import read_vnc_token, write_vnc_token

    token = read_vnc_token(docker_id)
    if not token or rotate:
        token = write_vnc_token(docker_id)
    return {"token": token}


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
        desktop_url=desktop_url,
        desk_id=getattr(emp, "desk_id", None),
        avatar_config=getattr(emp, "avatar_config", None),
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


@router.get("/workstations/assignments", response_model=list[WorkstationAssignmentResponse])
async def list_workstation_assignments(
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
):
    employees = await employee_repo.list_all()
    occupied_map = {emp.desk_id: emp for emp in employees if getattr(emp, "desk_id", None)}

    assignments: list[WorkstationAssignmentResponse] = []
    seen_desk_ids: set[str] = set()

    for ws in CANONICAL_WORKSTATIONS:
        desk_id = ws["id"]
        seen_desk_ids.add(desk_id)
        emp = occupied_map.get(desk_id)
        assignments.append(
            WorkstationAssignmentResponse(
                id=desk_id,
                name=ws["name"],
                department=ws["department"],
                role=ws["role"],
                desk_type=ws.get("desk_type", "single"),
                monitor_count=ws.get("monitor_count", 2),
                occupant_id=emp.employee_id if emp else None,
                occupant_name=emp.name if emp else None,
                occupant_status=emp.status.value if emp and hasattr(emp.status, "value") else (str(emp.status) if emp else None),
            )
        )

    # Include any overflow desks currently occupied
    for desk_id, emp in occupied_map.items():
        if desk_id not in seen_desk_ids:
            assignments.append(
                WorkstationAssignmentResponse(
                    id=desk_id,
                    name=f"Overflow Desk ({desk_id})",
                    department=emp.department,
                    role="overflow",
                    desk_type="single",
                    monitor_count=1,
                    occupant_id=emp.employee_id,
                    occupant_name=emp.name,
                    occupant_status=emp.status.value if hasattr(emp.status, "value") else str(emp.status),
                )
            )

    return assignments


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
    container_mgr: ContainerManager = Depends(get_container_manager),
    resource_lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
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
    if desktop_spec.get("status") == "runtime_unavailable":
        raise HTTPException(
            status_code=503,
            detail="Workstation runtime is unavailable; its durable lease remains reserved pending recovery.",
        )
    if desktop_spec.get("status") == "running":
        lease = await renew_workstation_lease(
            resource_lease_repo,
            employee.employee_id,
            desktop_spec["docker_id"],
        )
        if lease is None:
            raise HTTPException(
                status_code=409,
                detail="Workstation resource lease is expired, quarantined, or held by another session.",
            )

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
        started_at=desktop_spec.get("started_at"),
        desk_id=employee.desk_id,
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
    resource_lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
    actor: str = Depends(require_owner_identity),
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    # Approval consumption commits the shared AsyncSession and can expire ORM
    # instances. Snapshot all employee fields needed below before that commit.
    canonical_employee_id = employee.employee_id
    employee_config = dict(employee.model_config_data or {})
    docker_id = f"ancos-desktop-{canonical_employee_id.lower()}"
    lease = await claim_workstation_lease(
        resource_lease_repo,
        canonical_employee_id,
        docker_id,
    )
    if lease is None:
        raise HTTPException(
            status_code=409,
            detail="Desktop slot is already leased or quarantined pending cleanup.",
        )

    approval = await approval_repo.consume_once(
        approval_id,
        "PROVISION_WORKSTATION",
        {"employee_id": canonical_employee_id},
        actor,
    )
    if not approval:
        await resource_lease_repo.release(lease["lease_id"], lease["fencing_token"])
        raise HTTPException(status_code=409, detail="A current, approved, unused workstation-provision approval is required.")

    role_image = "employee-base"
    if "container_template" in employee_config:
        role_image = employee_config["container_template"]

    try:
        spec = await container_mgr.provision_employee_desktop(
            employee_id=canonical_employee_id,
            role_image=role_image,
            container_repo=container_repo,
            employee_repo=employee_repo
        )
    except DockerBrokerRequestError as exc:
        # A broker rejection can race with a successful create. Release only if
        # inspection confirms that the managed container is absent; otherwise
        # leave the durable lease active for TTL quarantine and reaper cleanup.
        try:
            await container_mgr._broker_request("GET", f"/v1/containers/{docker_id}")
        except DockerBrokerRequestError as inspect_error:
            if inspect_error.status_code == 404:
                await resource_lease_repo.release(lease["lease_id"], lease["fencing_token"])
        except Exception:
            pass
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception:
        # Network errors leave outcome uncertain. Keep the lease so a second
        # provision cannot overlap a workstation the broker may have started.
        raise

    await audit_repo.log(
        actor=actor,
        action="PROVISION_WORKSTATION",
        resource_type="Container",
        resource_id=spec["docker_id"],
        details={
            "employee_id": canonical_employee_id,
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
    resource_lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
    actor: str = Depends(require_owner_identity),
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    canonical_employee_id = employee.employee_id
    workstation = await container_mgr.get_desktop_by_employee_id(
        canonical_employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    if not workstation:
        raise HTTPException(status_code=404, detail=f"No workstation found for employee '{employee_id}'")
    if workstation.get("status") != "running":
        raise HTTPException(status_code=409, detail="Workstation must be running before rotating its VNC password")
    if await renew_workstation_lease(
        resource_lease_repo,
        canonical_employee_id,
        workstation["docker_id"],
    ) is None:
        raise HTTPException(status_code=409, detail="Workstation resource lease is expired or quarantined.")

    approval = await approval_repo.consume_once(
        approval_id,
        "ROTATE_VNC_PASSWORD",
        {"employee_id": canonical_employee_id, "docker_id": workstation["docker_id"]},
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
        details={"employee_id": canonical_employee_id, "approval_id": approval.id},
    )
    return VncCredentialResponse(
        employee_id=canonical_employee_id,
        docker_id=workstation["docker_id"],
        vnc_password=password,
    )


@router.get("/{employee_id}/workstation/vnc-token", response_model=VncTokenResponse)
@router.post("/{employee_id}/workstation/vnc-token", response_model=VncTokenResponse)
async def get_or_issue_vnc_token(
    employee_id: str,
    interactive: bool = Query(default=False),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
    actor: str = Depends(require_owner_identity),
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    canonical_employee_id = employee.employee_id
    workstation = await container_mgr.get_desktop_by_employee_id(
        canonical_employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    if not workstation:
        raise HTTPException(status_code=404, detail=f"No workstation found for employee '{employee_id}'")
    if workstation.get("status") != "running":
        raise HTTPException(status_code=409, detail="Workstation container must be running to issue a VNC token")

    docker_id = workstation["docker_id"]
    issued = await _issue_workstation_vnc_token(container_mgr, docker_id, rotate=interactive)
    token = str(issued["token"])

    view_only = not interactive
    novnc_port = workstation.get("novnc_port", 12003)
    desktop_url = issued.get("desktop_url") or (
        f"http://localhost:{novnc_port}/vnc.html?autoconnect=true&path=websockify%3Ftoken%3D{token}"
    )
    read_only_desktop_url = issued.get("read_only_desktop_url") or (
        f"http://localhost:{novnc_port}/vnc.html?autoconnect=true&view_only=true&path=websockify%3Ftoken%3D{token}"
    )

    await audit_repo.log(
        actor=actor,
        action="VNC_TOKEN_ISSUED",
        resource_type="Container",
        resource_id=workstation["docker_id"],
        details={
            "employee_id": canonical_employee_id,
            "view_only": view_only,
            "interactive_allowed": interactive,
        },
    )

    return VncTokenResponse(
        employee_id=canonical_employee_id,
        docker_id=workstation["docker_id"],
        token=token,
        ws_path=f"websockify?token={token}",
        desktop_url=desktop_url,
        read_only_desktop_url=read_only_desktop_url,
        view_only=view_only,
        interactive_allowed=interactive,
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
    resource_lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
    actor: str = Depends(require_owner_identity),
):
    employee = await employee_repo.get_by_employee_id(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")

    canonical_employee_id = employee.employee_id
    desktop_spec = await container_mgr.get_desktop_by_employee_id(
        canonical_employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo
    )
    if not desktop_spec:
        raise HTTPException(status_code=404, detail=f"No active workstation found for employee '{employee_id}'")

    docker_id = desktop_spec["docker_id"]
    approval = await approval_repo.consume_once(
        approval_id,
        "TERMINATE_WORKSTATION",
        {"employee_id": canonical_employee_id, "docker_id": docker_id},
        actor,
    )
    if not approval:
        raise HTTPException(status_code=409, detail="A current, approved, unused workstation-termination approval is required.")
    await container_mgr.terminate_desktop(docker_id, container_repo=container_repo)
    if not await release_workstation_lease(resource_lease_repo, docker_id):
        raise HTTPException(
            status_code=503,
            detail="Workstation was terminated, but its durable resource lease could not be released.",
        )

    await audit_repo.log(
        actor=actor,
        action="TERMINATE_WORKSTATION",
        resource_type="Container",
        resource_id=docker_id,
        details={"employee_id": canonical_employee_id, "approval_id": approval.id}
    )

    return {"status": "terminated", "docker_id": docker_id, "employee_id": canonical_employee_id}


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
            scheduled_at=t.scheduled_at,
            started_at=t.started_at,
            completed_at=t.completed_at
        )
        for t in tasks
    ]


@router.put("/{employee_id}/desk", response_model=EmployeeResponse)
async def assign_employee_desk(
    employee_id: str,
    payload: AssignDeskRequest,
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
):
    if not WorkstationAssignmentService.is_valid_desk_id(payload.desk_id):
        raise HTTPException(status_code=400, detail=f"Invalid desk identifier '{payload.desk_id}'")

    try:
        updated = await employee_repo.assign_desk(employee_id, payload.desk_id)
    except ValueError as e:
        msg = str(e)
        if "already assigned" in msg:
            raise HTTPException(status_code=409, detail=msg)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)

    desktop_spec = await container_mgr.get_desktop_by_employee_id(
        updated.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    desktop_url = desktop_spec.get("desktop_url") if desktop_spec else None
    return serialize_employee_model(updated, desktop_url=desktop_url)


@router.delete("/{employee_id}/desk", response_model=EmployeeResponse)
async def release_employee_desk(
    employee_id: str,
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
):
    try:
        updated = await employee_repo.release_desk(employee_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    desktop_spec = await container_mgr.get_desktop_by_employee_id(
        updated.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    desktop_url = desktop_spec.get("desktop_url") if desktop_spec else None
    return serialize_employee_model(updated, desktop_url=desktop_url)


@router.get("/{employee_id}/promotion-eligibility", response_model=EmployeePromotionEligibilityResponse)
async def get_employee_promotion_eligibility(
    employee_id: str,
    min_verified_tasks: int = 1,
    min_verified_skills: int = 1,
    min_pass_rate: float = 0.5,
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
):
    try:
        data = await employee_repo.evaluate_promotion_eligibility(
            employee_id=employee_id,
            min_verified_tasks=min_verified_tasks,
            min_verified_skills=min_verified_skills,
            min_pass_rate=min_pass_rate,
        )
        return EmployeePromotionEligibilityResponse(**data)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))


@router.post("/{employee_id}/promote", response_model=EmployeeResponse)
async def promote_employee_endpoint(
    employee_id: str,
    payload: PromoteEmployeeRequest,
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
    actor: str = Depends(require_owner_identity),
):
    mgr = EmployeePromotionManager(employee_repo=employee_repo, audit_repo=audit_repo)
    try:
        promoted = await mgr.promote(
            employee_id=employee_id,
            new_title=payload.new_title,
            new_model_tier=payload.new_model_tier,
            additional_tools=payload.additional_tools,
            reason=payload.reason,
            actor=actor,
            require_eligibility=True,
        )
    except ValueError as ve:
        msg = str(ve)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=409, detail=msg)

    desktop_spec = await container_mgr.get_desktop_by_employee_id(
        promoted.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    desktop_url = desktop_spec.get("desktop_url") if desktop_spec else None
    return serialize_employee_model(promoted, desktop_url=desktop_url)

