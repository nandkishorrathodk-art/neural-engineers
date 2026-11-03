from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_container_manager, get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.constants import ApprovalStatus
from neural.db.models import Employee, ResourceLease
from neural.db.repositories import ApprovalRepository
from sqlalchemy import select


class FakeContainerManager:
    def __init__(self):
        self.workstations = {}

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
            "status": "running",
            "workspace": f"/tmp/workspaces/{employee_id}",
            "started_at": "2026-11-03T00:00:00+00:00",
            "vnc_password": "test-only",
        }
        self.workstations[employee_id] = spec
        return spec

    async def get_desktop_by_employee_id(self, employee_id, **_kwargs):
        return self.workstations.get(employee_id)

    async def terminate_desktop(self, docker_id, container_repo=None):
        for spec in self.workstations.values():
            if spec["docker_id"] == docker_id:
                spec["status"] = "terminated"
                return None
        raise AssertionError("The test requested termination of an unknown workstation")


async def create_employee(session, employee_id: str):
    employee = Employee(
        employee_id=employee_id,
        name=f"Employee {employee_id}",
        title="Engineer",
        department="Engineering",
        system_prompt="Test employee",
        model_config_data={},
        approved_tools=["terminal"],
    )
    session.add(employee)
    await session.commit()
    await session.refresh(employee)
    return employee


async def create_provision_approval(session, employee_id: str):
    return await ApprovalRepository(session).create(
        action_type="PROVISION_WORKSTATION",
        description="Provision an approved employee desktop",
        justification="Authorized workstation request",
        requested_by="human-requester",
        details={"employee_id": employee_id},
        status=ApprovalStatus.APPROVED,
    )


@pytest.mark.asyncio
async def test_approved_workstation_lifecycle_claims_renews_and_releases_durable_slot(async_session):
    first = await create_employee(async_session, "ANC-EMP-LEASE-1")
    second = await create_employee(async_session, "ANC-EMP-LEASE-2")
    first_approval = await create_provision_approval(async_session, first.employee_id)
    second_approval = await create_provision_approval(async_session, second.employee_id)
    first_approval_id = first_approval.id
    second_approval_id = second_approval.id
    first_employee_id = first.employee_id
    second_employee_id = second.employee_id
    manager = FakeContainerManager()

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session
    app.dependency_overrides[get_container_manager] = lambda: manager
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provisioned = await client.post(
            f"/employees/{first_employee_id}/workstation/provision?approval_id={first_approval_id}"
        )
        assert provisioned.status_code == 200
        docker_id = provisioned.json()["docker_id"]

        blocked = await client.post(
            f"/employees/{second_employee_id}/workstation/provision?approval_id={second_approval_id}"
        )
        assert blocked.status_code == 409
        second_still_pending = await ApprovalRepository(async_session).get_by_id(second_approval_id)
        assert second_still_pending is not None
        assert second_still_pending.status == ApprovalStatus.APPROVED
        assert second_still_pending.consumed_at is None

        read = await client.get(f"/employees/{first_employee_id}/workstation")
        assert read.status_code == 200

        termination = await ApprovalRepository(async_session).create(
            action_type="TERMINATE_WORKSTATION",
            description="Terminate approved workstation",
            justification="Lifecycle test teardown",
            requested_by="human-requester",
            details={"employee_id": first_employee_id, "docker_id": docker_id},
            status=ApprovalStatus.APPROVED,
        )
        termination_id = termination.id
        terminated = await client.post(
            f"/employees/{first_employee_id}/workstation/terminate?approval_id={termination_id}"
        )
        assert terminated.status_code == 200

    rows = (
        await async_session.execute(
            select(ResourceLease).where(ResourceLease.session_id == docker_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "released"


@pytest.mark.asyncio
async def test_invalid_approval_releases_unprovisioned_workstation_reservation(async_session):
    employee = await create_employee(async_session, "ANC-EMP-LEASE-INVALID")
    employee_id = employee.employee_id
    manager = FakeContainerManager()
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session
    app.dependency_overrides[get_container_manager] = lambda: manager
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/employees/{employee_id}/workstation/provision?approval_id=missing-approval"
        )

    assert response.status_code == 409
    assert manager.workstations == {}
    leases = (
        await async_session.execute(
            select(ResourceLease).where(ResourceLease.employee_id == employee_id)
        )
    ).scalars().all()
    assert len(leases) == 1
    assert leases[0].status == "released"
