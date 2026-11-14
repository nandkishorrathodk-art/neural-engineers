"""NEURO-12C: the workstation-readiness trigger re-dispatches a stranded task.

Reproduces the second half of the live hybrid-008 failure at the API boundary: a
GUI/hybrid task is already PENDING (its earlier assignment was requeued by stale
recovery mid-provisioning), the approved workstation comes up, and provisioning
must immediately hand the task back to that employee instead of waiting for the
next worker poll.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_container_manager, get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.constants import (
    ApprovalStatus,
    ContainerStatus,
    EmployeeStatus,
    ExecutionMode,
    TaskStatus,
)
from neural.db.models import Employee, EmployeeSkill, Task
from neural.db.repositories import ApprovalRepository


class PersistingFakeContainerManager:
    """Mimics the real manager's DB persistence so readiness is observable."""

    def __init__(self):
        self.workstations: dict[str, dict] = {}

    async def provision_employee_desktop(
        self,
        employee_id,
        role_image="employee-base",
        container_repo=None,
        employee_repo=None,
    ):
        docker_id = f"ancos-desktop-{employee_id.lower()}"
        spec = {
            "docker_id": docker_id,
            "employee_id": employee_id,
            "image": f"ancos/{role_image}:latest",
            "vnc_port": 5901,
            "novnc_port": 6081,
            "desktop_url": "http://localhost:6081/vnc.html?autoconnect=true",
            "cpu_limit": 2.0,
            "mem_limit": "4g",
            "status": ContainerStatus.RUNNING.value,
            "workspace": f"/tmp/workspaces/{employee_id}",
            "started_at": "2026-11-15T00:00:00+00:00",
            "vnc_password": "test-only",
        }
        if container_repo is not None:
            record = await container_repo.upsert_current(
                docker_id=docker_id,
                image=spec["image"],
                status=ContainerStatus.RUNNING,
                vnc_port=spec["vnc_port"],
                novnc_port=spec["novnc_port"],
                workspace_path=spec["workspace"],
            )
            if employee_repo is not None:
                await employee_repo.assign_container(employee_id, record.id)
        self.workstations[employee_id] = spec
        return spec

    async def get_desktop_by_employee_id(self, employee_id, **_kwargs):
        return self.workstations.get(employee_id)


@pytest.mark.asyncio
async def test_provision_readiness_redispatches_pending_task(async_session):
    employee = Employee(
        employee_id="ANC-EMP-READY-DISPATCH",
        name="Waiting Engineer",
        title="Software Engineer",
        department="Engineering",
        system_prompt="Test employee",
        model_config_data={},
        approved_tools=["terminal", "gui"],
        execution_capabilities=["terminal", "gui"],
        status=EmployeeStatus.IDLE,
    )
    async_session.add(employee)
    await async_session.flush()
    async_session.add(EmployeeSkill(employee_id=employee.id, skill_name="gui", verified=True))
    async_session.add(
        Task(
            task_id="TASK-READY-DISPATCH",
            project_id="PROJ-READY-1",
            title="Stranded GUI work",
            objective="Resume once the desktop is up",
            scope="Readiness",
            acceptance_criteria="Task is assigned",
            execution_mode=ExecutionMode.HYBRID,
            status=TaskStatus.PENDING,
            assigned_to=None,
            fencing_token=2,
            required_capabilities=["gui"],
        )
    )
    approval = await ApprovalRepository(async_session).create(
        action_type="PROVISION_WORKSTATION",
        description="Provision the approved workstation",
        justification="Readiness redispatch test",
        requested_by="human-requester",
        details={"employee_id": employee.employee_id},
        status=ApprovalStatus.APPROVED,
    )
    await async_session.commit()
    employee_id = employee.employee_id
    approval_id = approval.id
    employee_pk = employee.id

    manager = PersistingFakeContainerManager()
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session
    app.dependency_overrides[get_container_manager] = lambda: manager
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/employees/{employee_id}/workstation/provision?approval_id={approval_id}"
        )
    assert response.status_code == 200

    refreshed = (
        await async_session.execute(
            Task.__table__.select().where(Task.task_id == "TASK-READY-DISPATCH")
        )
    ).mappings().one()
    assert refreshed["status"] == TaskStatus.ASSIGNED.value
    assert refreshed["assigned_to"] == employee_pk
    assert refreshed["assigned_at"] is not None
    assert refreshed["fencing_token"] == 3
