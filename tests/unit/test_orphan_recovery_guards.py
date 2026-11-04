from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_container_manager, get_db_session, get_resource_lease_repo
from neural.api.main import create_app
from neural.config import get_settings
from neural.constants import EmployeeStatus, TaskStatus
from neural.containers.manager import DockerBrokerRequestError
from neural.db.models import TaskAttempt
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository

OWNER_ID = "orphan-guard-owner"
OWNER_TOKEN = "orphan-guard-owner-" + "o" * 40


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocker", "expected_status"),
    [
        ("attempt_history", 409),
        ("workstation_lease", 409),
        ("broker_unavailable", 503),
        ("desktop_still_exists", 409),
    ],
)
async def test_orphan_recovery_preflight_fails_closed_for_attempt_lease_and_broker_uncertainty(
    async_session,
    monkeypatch,
    blocker,
    expected_status,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("APPROVER_API_TOKEN", "orphan-guard-approver-" + "a" * 40)
    get_settings.cache_clear()

    employee = await EmployeeRepository(async_session).create(
        employee_id=f"ANC-EMP-ORPHAN-GUARD-{blocker.upper()}",
        name="Orphan Guard Employee",
        title="Software Engineer",
        system_prompt="Test recovery guard",
        status=EmployeeStatus.WORKING,
    )
    project = await ProjectRepository(async_session).create(
        name=f"Orphan guard {blocker}",
        objective="Block unsafe orphan recovery preflight",
    )
    task = await TaskRepository(async_session).create(
        task_id=f"TASK-ORPHAN-GUARD-{blocker.upper()}",
        project_id=project.id,
        assigned_to=employee.id,
        title="Fail closed on recovery blocker",
        objective="Do not request approval while recovery preconditions are unsafe",
        scope="Recovery guard test",
        acceptance_criteria="The route rejects unsafe recovery preconditions",
        status=TaskStatus.RUNNING,
        fencing_token=23,
    )
    if blocker == "attempt_history":
        async_session.add(
            TaskAttempt(
                task_id=task.id,
                attempt_number=1,
                worker_id="old-worker",
                fencing_token=22,
                status="failed",
                lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        await async_session.commit()

    class LeaseRepoStub:
        async def list_blocking_for_employee(self, **_kwargs):
            return [{"status": "active"}] if blocker == "workstation_lease" else []

    class BrokerStub:
        def __init__(self):
            self.calls = []

        async def _broker_request(self, method, path):
            self.calls.append((method, path))
            if blocker == "broker_unavailable":
                raise DockerBrokerRequestError("broker unavailable", 500)
            if blocker == "desktop_still_exists":
                return {"status": "running"}
            raise AssertionError("Broker must not be called when a database precondition blocks recovery.")

    broker = BrokerStub()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_resource_lease_repo] = lambda: LeaseRepoStub()
    app.dependency_overrides[get_container_manager] = lambda: broker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/boss/tasks/{task.task_id}/recovery-approvals",
            json={"justification": "The assigned task appears abandoned."},
            headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        )
    get_settings.cache_clear()

    assert response.status_code == expected_status
    assert broker.calls == ([] if blocker in {"attempt_history", "workstation_lease"} else [
        ("GET", f"/v1/containers/ancos-desktop-{employee.employee_id.lower()}")
    ])
