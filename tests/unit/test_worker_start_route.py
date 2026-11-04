import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_container_manager, get_db_session
from neural.api.main import create_app
from neural.config import get_settings
from neural.constants import (
    ContainerStatus,
    EmployeeStatus,
    ExecutionMode,
    TaskPriority,
    TaskStatus,
)
from neural.db.models import TaskAttempt
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from sqlalchemy import select

WORKER_ID = "worker-start-test"
WORKER_TOKEN = "worker-start-route-" + "w" * 40


def _complete_manifest() -> dict:
    return {
        "captured_at_unix": 1790000000.0,
        "files": [],
        "file_count": 0,
        "total_size_bytes": 0,
        "complete": True,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_worker_start_captures_and_persists_task_workspace_baseline(async_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-WORKER-START",
        name="Worker Start Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-worker-start",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/worker-start-validation",
    )
    await employee_repo.assign_container(employee.employee_id, container.id)
    await async_session.refresh(employee)
    project = await ProjectRepository(async_session).create(
        name="Worker start baseline",
        objective="Capture the task-start workspace before execution",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-WORKER-START-BASELINE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Capture workspace baseline",
        objective="Persist a complete snapshot before work starts",
        scope="Worker route validation",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        priority=TaskPriority.HIGH,
        status=TaskStatus.ASSIGNED,
    )
    claim = await TaskRepository(async_session).claim_next_task(
        WORKER_ID,
        execution_modes=[ExecutionMode.TERMINAL],
    )
    assert claim is not None

    class ContainerManagerStub:
        def __init__(self):
            self.events = []

        async def get_desktop_by_employee_id(self, employee_id, **_kwargs):
            self.events.append(("desktop", employee_id))
            return {"status": "running", "docker_id": container.docker_id}

        async def capture_verifier_manifest(self, employee_id):
            self.events.append(("manifest", employee_id))
            return _complete_manifest()

    manager = ContainerManagerStub()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_container_manager] = lambda: manager
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/start",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    assert manager.events == [("desktop", employee.employee_id), ("manifest", employee.employee_id)]
    attempt = await async_session.scalar(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
    assert attempt is not None
    assert attempt.status == "running"
    assert attempt.workspace_manifest_before == _complete_manifest()


@pytest.mark.asyncio
async def test_worker_start_fails_closed_and_releases_claim_for_invalid_baseline(async_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-WORKER-START-INVALID",
        name="Worker Start Invalid Baseline",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-worker-start-invalid",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/worker-start-invalid-validation",
    )
    await employee_repo.assign_container(employee.employee_id, container.id)
    await async_session.refresh(employee)
    project = await ProjectRepository(async_session).create(
        name="Worker start invalid baseline",
        objective="Do not start when the workspace snapshot is incomplete",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-WORKER-START-INVALID",
        project_id=project.id,
        assigned_to=employee.id,
        title="Reject incomplete workspace baseline",
        objective="Keep task unstarted when evidence is incomplete",
        scope="Worker route validation",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        priority=TaskPriority.HIGH,
        status=TaskStatus.ASSIGNED,
    )
    task_repo = TaskRepository(async_session)
    claim = await task_repo.claim_next_task(WORKER_ID, execution_modes=[ExecutionMode.TERMINAL])
    assert claim is not None

    class ContainerManagerStub:
        async def get_desktop_by_employee_id(self, _employee_id, **_kwargs):
            return {"status": "running", "docker_id": container.docker_id}

        async def capture_verifier_manifest(self, _employee_id):
            return {**_complete_manifest(), "complete": False, "errors": ["size limit exceeded"]}

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_container_manager] = lambda: ContainerManagerStub()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/start",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 503
    attempt = await async_session.scalar(select(TaskAttempt).where(TaskAttempt.task_id == task.id))
    assert attempt is not None
    assert attempt.status == "waiting"
    assert attempt.workspace_manifest_before is None


@pytest.mark.asyncio
async def test_worker_start_rejects_non_owner_lease_before_broker_calls(async_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-WORKER-START-FENCE",
        name="Worker Start Fence Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-worker-start-fence",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/worker-start-fence-validation",
    )
    await employee_repo.assign_container(employee.employee_id, container.id)
    await async_session.refresh(employee)
    project = await ProjectRepository(async_session).create(
        name="Worker start lease fence",
        objective="Reject a different worker's lease before broker access",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-WORKER-START-FENCE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Reject stale worker",
        objective="Only the lease owner can start the task",
        scope="Worker route validation",
        acceptance_criteria="Automated tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        priority=TaskPriority.HIGH,
        status=TaskStatus.ASSIGNED,
    )
    claim = await TaskRepository(async_session).claim_next_task(
        "another-worker",
        execution_modes=[ExecutionMode.TERMINAL],
    )
    assert claim is not None

    class ContainerManagerStub:
        def __init__(self):
            self.calls = 0

        async def get_desktop_by_employee_id(self, *_args, **_kwargs):
            self.calls += 1
            return {"status": "running", "docker_id": container.docker_id}

        async def capture_verifier_manifest(self, _employee_id):
            self.calls += 1
            return _complete_manifest()

    manager = ContainerManagerStub()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_container_manager] = lambda: manager
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/start",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 409
    assert manager.calls == 0
