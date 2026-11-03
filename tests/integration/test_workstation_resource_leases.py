from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_container_manager, get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.constants import ApprovalStatus, ContainerStatus
from neural.db.models import Employee, ResourceLease
from neural.db.repositories import ApprovalRepository, ContainerRepository, ResourceLeaseRepository
from neural.resource_scheduler.workstation_lease import claim_workstation_lease
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
async def test_workstation_runtime_outage_returns_503_and_retains_durable_lease(async_session):
    employee = await create_employee(async_session, "ANC-EMP-LEASE-OUTAGE")
    approval = await create_provision_approval(async_session, employee.employee_id)
    manager = FakeContainerManager()
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session
    app.dependency_overrides[get_container_manager] = lambda: manager
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provisioned = await client.post(
            f"/employees/{employee.employee_id}/workstation/provision?approval_id={approval.id}"
        )
        assert provisioned.status_code == 200
        docker_id = provisioned.json()["docker_id"]
        manager.workstations[employee.employee_id]["status"] = "runtime_unavailable"

        unavailable = await client.get(f"/employees/{employee.employee_id}/workstation")

    assert unavailable.status_code == 503
    assert "runtime is unavailable" in unavailable.json()["detail"].lower()
    rows = (
        await async_session.execute(
            select(ResourceLease).where(ResourceLease.session_id == docker_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "active"


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


@pytest.mark.asyncio
async def test_configured_workstation_capacity_claims_distinct_durable_slots(async_session, monkeypatch):
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "2")
    employee_ids = [f"ANC-EMP-LEASE-CAP-{index}" for index in range(1, 4)]
    for employee_id in employee_ids:
        await create_employee(async_session, employee_id)

    repository = ResourceLeaseRepository(async_session)
    leases = [
        await claim_workstation_lease(
            repository,
            employee_id,
            f"ancos-desktop-{employee_id.lower()}",
        )
        for employee_id in employee_ids
    ]

    assert leases[0] is not None
    assert leases[1] is not None
    assert leases[2] is None
    leases = (
        await async_session.execute(
            select(ResourceLease).where(ResourceLease.employee_id.in_(employee_ids))
        )
    ).scalars().all()
    active_leases = [lease for lease in leases if lease.status == "active"]
    assert len(active_leases) == 2
    assert len({lease.resource_key for lease in active_leases}) == 2


@pytest.mark.asyncio
async def test_session_lookup_prefers_active_lease_after_pool_change(async_session):
    repository = ResourceLeaseRepository(async_session)
    employee_id = "ANC-EMP-LEASE-POOL-CHANGE"
    session_id = f"ancos-desktop-{employee_id.lower()}"
    previous = await repository.claim(
        resource_key="desktop:previous-pool:slot-1",
        employee_id=employee_id,
        session_id=session_id,
        lease_seconds=60,
    )
    assert previous is not None
    assert await repository.release(previous["lease_id"], previous["fencing_token"])

    current = await repository.claim(
        resource_key="desktop:current-pool:slot-1",
        employee_id=employee_id,
        session_id=session_id,
        lease_seconds=60,
    )
    assert current is not None

    found = await repository.get_by_session_id(session_id)
    assert found is not None
    assert found["lease_id"] == current["lease_id"]
    assert found["status"] == "active"
    assert await repository.release(found["lease_id"], found["fencing_token"])


@pytest.mark.asyncio
async def test_container_upsert_reuses_terminated_docker_record(async_session):
    repository = ContainerRepository(async_session)
    docker_id = "ancos-desktop-anc-emp-reused"
    original = await repository.create(
        docker_id=docker_id,
        image="ancos/employee-base:latest",
        status=ContainerStatus.TERMINATED,
        vnc_port=5901,
        novnc_port=6081,
        resource_limits={"cpu_limit": 2.0, "mem_limit": "4g"},
        network_config={"network": "ancos-bridge-net"},
        workspace_path="ancos-workspace-anc-emp-reused",
    )
    original_started_at = original.started_at

    current = await repository.upsert_current(
        docker_id=docker_id,
        image="ancos/employee-qa:latest",
        status=ContainerStatus.RUNNING,
        vnc_port=5902,
        novnc_port=6082,
        resource_limits={"cpu_limit": 1.0, "mem_limit": "1g"},
        network_config={"network": "ancos-bridge-net"},
        workspace_path="ancos-workspace-anc-emp-reused",
    )

    rows = await repository.list_all()
    assert current.id == original.id
    assert current.status == ContainerStatus.RUNNING
    assert current.image == "ancos/employee-qa:latest"
    assert current.vnc_port == 5902
    assert current.started_at > original_started_at
    assert len(rows) == 1
