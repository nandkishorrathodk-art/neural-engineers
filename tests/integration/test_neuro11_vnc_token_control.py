from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_container_manager, get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.constants import ContainerStatus
from neural.containers.broker_api import (
    read_vnc_token,
    revoke_vnc_token,
    write_vnc_token,
)
from neural.db.repositories import (
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
)
from neural.employees.factory import EmployeeFactory
from sqlalchemy.ext.asyncio import AsyncSession


def test_vnc_token_generation_and_revocation(tmp_path, monkeypatch):
    monkeypatch.setattr("neural.containers.broker_api.token_file_path", lambda: tmp_path / "tokens")

    container_name = "ancos-desktop-anc-emp-test01"

    # No token initially
    assert read_vnc_token(container_name) is None

    # Write token
    token = write_vnc_token(container_name)
    assert token is not None
    assert len(token) > 16

    # Read back token
    retrieved = read_vnc_token(container_name)
    assert retrieved == token

    # Revoke token
    revoke_vnc_token(container_name)
    assert read_vnc_token(container_name) is None


@pytest.mark.asyncio
async def test_vnc_token_audit_logging_and_least_privilege(
    async_session: AsyncSession,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("neural.containers.broker_api.token_file_path", lambda: tmp_path / "tokens")

    emp_repo = EmployeeRepository(async_session)
    container_repo = ContainerRepository(async_session)
    audit_repo = AuditRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    employee = await factory.hire_employee(
        name="Security Tester",
        title="Software Engineer",
        department="Engineering",
        desk_id="WS-ENG-03",
    )

    docker_id = f"ancos-desktop-{employee.employee_id.lower()}"

    # Provision container record in DB
    container = await container_repo.create(
        docker_id=docker_id,
        image="employee-swe",
        status=ContainerStatus.RUNNING,
        vnc_port=5901,
        novnc_port=12003,
        workspace_path="/tmp/workspace",
    )
    employee.container_id = container.id
    await async_session.commit()

    # Generate token
    token = write_vnc_token(docker_id)
    assert token is not None

    # Log audit event
    await audit_repo.log(
        actor="operator",
        action="VNC_TOKEN_ISSUED",
        resource_type="Container",
        resource_id=docker_id,
        details={
            "employee_id": employee.employee_id,
            "view_only": True,
            "interactive_allowed": False,
        },
    )

    # Verify audit event recorded
    vnc_logs = await audit_repo.list_by_action("VNC_TOKEN_ISSUED", limit=5)
    assert len(vnc_logs) >= 1
    details = vnc_logs[0].details
    assert details["employee_id"] == employee.employee_id
    assert details["view_only"] is True


@pytest.mark.asyncio
async def test_vnc_token_api_endpoint_roundtrip(
    async_session: AsyncSession,
    tmp_path,
    monkeypatch,
):
    """
    Empirically tests the REST API endpoint GET & POST /employees/{id}/workstation/vnc-token:
    - 404 on nonexistent employee
    - 409 when employee container is offline/stopped
    - 200 with default view_only=True and least-privilege token
    - 200 with interactive=True issuing elevated token
    - Verifies real audit logging in database.
    """
    monkeypatch.setattr("neural.containers.broker_api.token_file_path", lambda: tmp_path / "tokens")

    emp_repo = EmployeeRepository(async_session)
    audit_repo = AuditRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    employee = await factory.hire_employee(
        name="REST VNC Tester",
        title="Infrastructure Engineer",
        department="Engineering",
        desk_id="WS-ENG-02",
    )

    docker_id = f"ancos-desktop-{employee.employee_id.lower()}"
    container_state: dict[str, str | None] = {"status": None}

    class FakeContainerManager:
        async def get_desktop_by_employee_id(self, emp_id, **kwargs):
            if emp_id != employee.employee_id or container_state["status"] is None:
                return None
            return {
                "docker_id": docker_id,
                "status": container_state["status"],
                "novnc_port": 12003,
            }

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"
    app.dependency_overrides[get_container_manager] = lambda: FakeContainerManager()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # 1. Nonexistent employee -> 404
        res_404 = await client.get("/employees/ANC-EMP-NONEXISTENT/workstation/vnc-token")
        assert res_404.status_code == 404

        # 2. Employee has no workstation spec -> 404
        res_no_ws = await client.get(f"/employees/{employee.employee_id}/workstation/vnc-token")
        assert res_no_ws.status_code == 404

        # 3. Workstation container in STOPPED status -> 409
        container_state["status"] = "stopped"
        res_stopped = await client.get(f"/employees/{employee.employee_id}/workstation/vnc-token")
        assert res_stopped.status_code == 409

        # 4. Workstation container in RUNNING status -> 200 with default view_only=True
        container_state["status"] = "running"
        res_ok = await client.get(f"/employees/{employee.employee_id}/workstation/vnc-token")
        assert res_ok.status_code == 200
        data_view = res_ok.json()
        assert data_view["employee_id"] == employee.employee_id
        assert data_view["docker_id"] == docker_id
        assert data_view["view_only"] is True
        assert data_view["interactive_allowed"] is False
        assert "token" in data_view
        assert data_view["token"] in data_view["read_only_desktop_url"]
        assert "view_only=true" in data_view["read_only_desktop_url"]

        # 5. POST with interactive=True -> 200 with view_only=False
        res_interactive = await client.post(f"/employees/{employee.employee_id}/workstation/vnc-token?interactive=true")
        assert res_interactive.status_code == 200
        data_interactive = res_interactive.json()
        assert data_interactive["view_only"] is False
        assert data_interactive["interactive_allowed"] is True

        # 6. Verify audit logs
        logs = await audit_repo.list_by_action("VNC_TOKEN_ISSUED", limit=10)
        assert len(logs) >= 2
        view_audits = [entry for entry in logs if entry.details.get("view_only") is True]
        interactive_audits = [entry for entry in logs if entry.details.get("interactive_allowed") is True]
        assert len(view_audits) >= 1
        assert len(interactive_audits) >= 1


@pytest.mark.asyncio
async def test_vnc_token_api_preserves_broker_desktop_url_with_password(
    async_session: AsyncSession,
    tmp_path,
    monkeypatch,
):
    """The broker-minted URLs (carrying the VNC password) must survive the route."""
    monkeypatch.setattr("neural.containers.broker_api.token_file_path", lambda: tmp_path / "tokens")

    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)
    employee = await factory.hire_employee(
        name="Broker URL Tester",
        title="Platform Engineer",
        department="Engineering",
        desk_id="WS-ENG-02",
    )
    docker_id = f"ancos-desktop-{employee.employee_id.lower()}"
    broker_urls = {
        "token": "broker-token",
        "desktop_url": "http://localhost:12003/vnc.html?autoconnect=true&path=x&password=abc12345",
        "read_only_desktop_url": "http://localhost:12003/vnc.html?view_only=true&password=abc12345",
    }

    class FakeContainerManager:
        async def get_desktop_by_employee_id(self, emp_id, **kwargs):
            if emp_id != employee.employee_id:
                return None
            return {"docker_id": docker_id, "status": "running", "novnc_port": 12003}

        async def get_or_issue_vnc_token(self, target_docker_id, *, rotate=False):
            assert target_docker_id == docker_id
            return broker_urls

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"
    app.dependency_overrides[get_container_manager] = lambda: FakeContainerManager()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        res = await client.get(f"/employees/{employee.employee_id}/workstation/vnc-token")

    assert res.status_code == 200
    data = res.json()
    assert data["token"] == "broker-token"
    assert data["desktop_url"] == broker_urls["desktop_url"]
    assert data["read_only_desktop_url"] == broker_urls["read_only_desktop_url"]
    assert "password=abc12345" in data["read_only_desktop_url"]

