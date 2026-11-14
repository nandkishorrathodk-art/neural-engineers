import base64
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session, get_llm_gateway
from neural.api.main import create_app
from neural.config import get_settings
from neural.constants import ContainerStatus, EmployeeStatus, ExecutionMode, TaskStatus
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.workers.terminal import TerminalPlan, WorkspaceFile, _write_commands

WORKER_ID = "worker-llm-proxy-test"
WORKER_TOKEN = "worker-llm-proxy-test-" + "w" * 40


@pytest.mark.asyncio
async def test_terminal_plan_is_generated_by_api_for_current_fenced_worker(
    async_session, monkeypatch
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-LLM-PROXY",
        name="LLM Proxy Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-llm-proxy",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/llm-proxy-validation",
    )
    await EmployeeRepository(async_session).assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="LLM proxy",
        objective="Route worker planning through the core API",
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-LLM-PROXY",
        project_id=project.id,
        assigned_to=employee.id,
        title="Create test_clamp.py with clamp and unittest cases",
        objective="Implement and persist test_clamp.py in the workspace",
        scope="Task workspace",
        acceptance_criteria="Tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
        resource_budget={"max_tokens": 500},
    )
    claim = await task_repo.claim_next_task(WORKER_ID, execution_modes=[ExecutionMode.TERMINAL])
    assert claim is not None
    await task_repo.start_task_attempt(task.task_id, WORKER_ID, claim["fencing_token"], {
        "captured_at_unix": 1790000000.0,
        "files": [],
        "file_count": 0,
        "total_size_bytes": 0,
        "complete": True,
        "errors": [],
    })

    class GatewayStub:
        calls = []
        plans = [
            TerminalPlan(commands=[["python3", "-c", "class TestClamp: pass"]]),
            TerminalPlan(
                files=[WorkspaceFile(path="test_clamp.py", content="value = 1")],
                commands=[["python3", "-m", "unittest", "-v"]],
            ),
        ]
        cost_tracker = SimpleNamespace(
            max_project_budget_usd=150.0,
            max_task_budget_usd=10.0,
            monthly_hard_cap_usd=500.0,
        )

        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            plan = self.plans[len(self.calls) - 1]
            return plan, SimpleNamespace(
                provider="test-provider",
                model="test-model",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
            )

    gateway = GatewayStub()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["plan"] == {
        "commands": [
            *_write_commands("test_clamp.py", "value = 1"),
            ["python3", "-m", "unittest", "-v"],
        ]
    }
    assert response.json()["model"]["provider"] == "test-provider"
    assert len(gateway.calls) == 2
    assert gateway.calls[0]["task_id"] == task.task_id
    assert gateway.calls[0]["project_id"] == project.id
    assert gateway.calls[0]["max_tokens"] == 500
    assert gateway.calls[0]["db_session"] is async_session
    assert "Return `commands` as a JSON array of argv arrays" in gateway.calls[0]["prompt"]
    assert "Put the COMPLETE raw file contents here" in gateway.calls[0]["prompt"]
    assert "do not inline test definitions in the test-running command" in gateway.calls[0]["prompt"]
    assert "Put complete file contents in the `files` field" in gateway.calls[0]["system_prompt"]
    assert "previous plan was rejected before execution" in gateway.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_prepare_phase_strips_the_gui_owned_line_from_the_artifact(async_session, monkeypatch):
    """The GUI phase types this line later; prepare must not also persist it."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-LLM-HYBRID",
        name="Hybrid Prepare Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
        execution_capabilities=["terminal", "gui"],
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-llm-hybrid",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/llm-hybrid-validation",
    )
    await EmployeeRepository(async_session).assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="Hybrid prepare",
        objective="Prepare an artifact the desktop phase will extend",
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-LLM-HYBRID",
        project_id=project.id,
        assigned_to=employee.id,
        title="Prepare handoff.txt then edit it on the desktop",
        objective="Create handoff.txt, edit it graphically, then verify",
        scope="Task workspace",
        acceptance_criteria="handoff.txt contains the prepared and edited lines",
        execution_mode=ExecutionMode.HYBRID,
        status=TaskStatus.ASSIGNED,
        resource_budget={"max_tokens": 500},
    )
    claim = await task_repo.claim_next_task(WORKER_ID, execution_modes=[ExecutionMode.HYBRID])
    assert claim is not None
    await task_repo.start_task_attempt(task.task_id, WORKER_ID, claim["fencing_token"], {
        "captured_at_unix": 1790000000.0,
        "files": [],
        "file_count": 0,
        "total_size_bytes": 0,
        "complete": True,
        "errors": [],
    })

    class GatewayStub:
        calls = []
        cost_tracker = SimpleNamespace(
            max_project_budget_usd=150.0,
            max_task_budget_usd=10.0,
            monthly_hard_cap_usd=500.0,
        )

        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            # The model disobeys the prompt and writes the GUI-owned line itself.
            plan = TerminalPlan(
                files=[
                    WorkspaceFile(
                        path="handoff.txt",
                        content=(
                            "ANCOS_HYBRID_TEST\nTERMINAL_PREPARED=1\nGUI_EDIT=HYBRID_GUI_OK\n"
                        ),
                    )
                ],
                commands=[],
            )
            return plan, SimpleNamespace(
                provider="test-provider",
                model="test-model",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
            )

    gateway = GatewayStub()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={
                "fencing_token": claim["fencing_token"],
                "phase": "prepare",
                "exclude_text": "GUI_EDIT=HYBRID_GUI_OK",
            },
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    commands = response.json()["plan"]["commands"]
    written = "".join(
        base64.b64decode(part.split("b64decode('")[1].split("')")[0]).decode()
        for part in (command[2] for command in commands)
    )
    assert "ANCOS_HYBRID_TEST" in written
    assert "TERMINAL_PREPARED=1" in written
    assert "GUI_EDIT=HYBRID_GUI_OK" not in written
    # The graphical phase appends by typing at the end of the document; the
    # prepared artifact must end with exactly one newline so the append lands as
    # a clean new line (no concatenation, no blank line).
    assert written.endswith("\n")
    assert not written.endswith("\n\n")


@pytest.mark.asyncio
async def test_prepare_phase_normalizes_raw_write_commands(async_session, monkeypatch):
    """When the model emits the artifact as a raw base64 write command instead of a
    `files` entry, the prepare phase must still strip the GUI line and end the file
    with exactly one newline."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-LLM-RAW",
        name="Hybrid Raw Command Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
        execution_capabilities=["terminal", "gui"],
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-llm-raw",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/llm-hybrid-raw",
    )
    await EmployeeRepository(async_session).assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="Hybrid raw prepare",
        objective="Prepare an artifact the desktop phase will extend",
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-LLM-RAW",
        project_id=project.id,
        assigned_to=employee.id,
        title="Prepare handoff.txt then edit it on the desktop",
        objective="Create handoff.txt, edit it graphically, then verify",
        scope="Task workspace",
        acceptance_criteria="handoff.txt contains the prepared and edited lines",
        execution_mode=ExecutionMode.HYBRID,
        status=TaskStatus.ASSIGNED,
        resource_budget={"max_tokens": 500},
    )
    claim = await task_repo.claim_next_task(WORKER_ID, execution_modes=[ExecutionMode.HYBRID])
    assert claim is not None
    await task_repo.start_task_attempt(task.task_id, WORKER_ID, claim["fencing_token"], {
        "captured_at_unix": 1790000000.0,
        "files": [],
        "file_count": 0,
        "total_size_bytes": 0,
        "complete": True,
        "errors": [],
    })

    class GatewayStub:
        calls = []
        cost_tracker = SimpleNamespace(
            max_project_budget_usd=150.0,
            max_task_budget_usd=10.0,
            monthly_hard_cap_usd=500.0,
        )

        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            # No `files` entry: the model wrote the artifact inline instead, with no
            # trailing newline and including the GUI-owned line.
            encoded = base64.b64encode(
                b"ANCOS_HYBRID_TEST\nTERMINAL_PREPARED=1\nGUI_EDIT=HYBRID_GUI_OK"
            ).decode()
            plan = TerminalPlan(
                files=[],
                commands=[
                    [
                        "python3",
                        "-c",
                        f"import base64; open('handoff.txt', 'wb').write(base64.b64decode('{encoded}'))",
                    ]
                ],
            )
            return plan, SimpleNamespace(
                provider="test-provider",
                model="test-model",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
            )

    gateway = GatewayStub()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={
                "fencing_token": claim["fencing_token"],
                "phase": "prepare",
                "exclude_text": "GUI_EDIT=HYBRID_GUI_OK",
            },
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    commands = response.json()["plan"]["commands"]
    written = "".join(
        base64.b64decode(part.split("b64decode('")[1].split("')")[0]).decode()
        for part in (command[2] for command in commands)
    )
    assert written == "ANCOS_HYBRID_TEST\nTERMINAL_PREPARED=1\n"


@pytest.mark.asyncio
async def test_terminal_plan_rejects_unleased_task_before_provider_call(async_session, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    class GatewayStub:
        called = False

        async def generate_structured(self, **_kwargs):
            self.called = True
            raise AssertionError("stale workers must not trigger billable provider calls")

    gateway = GatewayStub()
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-LLM-UNLEASED",
        name="Unleased LLM Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    project = await ProjectRepository(async_session).create(
        name="Unleased LLM proxy task",
        objective="Reject planning calls without a current worker lease",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-LLM-UNLEASED",
        project_id=project.id,
        assigned_to=employee.id,
        title="Unleased task",
        objective="Should not be planned",
        scope="None",
        acceptance_criteria="Provider is not called",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
    )
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": 999},
        )
    get_settings.cache_clear()

    assert response.status_code == 409
    assert gateway.called is False
