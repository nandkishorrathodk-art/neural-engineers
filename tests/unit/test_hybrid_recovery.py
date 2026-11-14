"""Regression tests for NEURO-12B Scope B.

Two failure classes were reported in NEURO-12A:

1. A GUI-execute 409 ("Assigned employee workstation is not live" / "Live desktop
   became unavailable mid-task") was reported as a *submitted, failed* task
   receipt. The task's own work never ran, yet it consumed the retry budget and
   the GUI phase was replayed blindly on the next attempt.
2. A desktop that vanished between phases lost all record of the phases that had
   already durably completed.

These tests lock in the fix: an unavailable desktop is classified as an
infrastructure failure, reported via ``/fail`` (requeue, no budget charge), and
durably completed phases are checkpointed so they are never replayed.
"""

from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_checkpoint_manager, get_db_session
from neural.api.main import create_app
from neural.config import get_settings
from neural.constants import ContainerStatus, EmployeeStatus, ExecutionMode, TaskStatus
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.resource_scheduler.checkpoint_manager import CheckpointManager
from neural.workers.gui import GuiWorker
from neural.workers.hybrid import (
    HybridExecutionCoordinator,
    HybridInfrastructureError,
    _is_infrastructure_failure,
)


def _http_error(status_code: int, text: str = "conflict") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://core/internal/worker/tasks/T/gui/execute")
    response = httpx.Response(status_code, text=text, request=request)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


def test_desktop_unavailable_status_is_infrastructure_failure():
    assert _is_infrastructure_failure(_http_error(409, "Assigned employee workstation is not live."))
    assert _is_infrastructure_failure(
        _http_error(409, "Live desktop became unavailable mid-task: container gone")
    )
    assert _is_infrastructure_failure(_http_error(503, "temporarily unavailable"))


def test_environment_markers_are_infrastructure_failures():
    assert _is_infrastructure_failure(
        HybridInfrastructureError("[gui] live desktop unavailable: no live desktop")
    )
    assert _is_infrastructure_failure(RuntimeError("workstation is not live"))


def test_ordinary_task_failure_is_not_infrastructure():
    assert not _is_infrastructure_failure(RuntimeError("pytest exited with code 1: 2 failed"))
    assert not _is_infrastructure_failure(_http_error(422, "desktop interaction failed"))


class _FakeGuiWorker:
    """A worker whose Core API calls are served from an in-memory route table."""

    display = ":1"

    def __init__(self, *, gui_execute_error: httpx.HTTPStatusError | None = None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.gui_execute_error = gui_execute_error
        self.checkpoint: dict | None = None
        self.checkpoint_writes: list[dict] = []

    async def _post(self, path: str, payload: dict) -> dict:
        self.calls.append(("POST", path, payload))
        if path.endswith("/plan"):
            return {"plan": {"files": [], "commands": [["python3", "-c", "print('ok')"]]}}
        if path.endswith("/exec"):
            return {"exit_code": 0, "stdout": "ok", "stderr": ""}
        if path.endswith("/gui/plan"):
            return {"steps": [{"action_type": "KEY_TYPE", "text": "hello"}], "expected_text": "hello"}
        if path.endswith("/gui/execute"):
            if self.gui_execute_error is not None:
                raise self.gui_execute_error
            return {"result": {"status": "success", "steps_executed": 1}}
        if path.endswith("/checkpoint"):
            self.checkpoint_writes.append(payload)
            self.checkpoint = {
                "completed_phases": payload["completed_phases"],
                "protected_paths": payload["protected_paths"],
            }
            return {"task_id": "T", "phase": payload["phase"]}
        raise AssertionError(f"unexpected POST {path}")

    async def _get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append(("GET", path, params or {}))
        if path.endswith("/checkpoint"):
            return {"task_id": "T", "checkpoint": self.checkpoint}
        raise AssertionError(f"unexpected GET {path}")


def _task() -> dict:
    return {"task_id": "T", "fencing_token": 3, "execution_mode": "hybrid"}


def _paths(worker: _FakeGuiWorker) -> list[str]:
    return [path for _, path, _ in worker.calls]


@pytest.mark.asyncio
async def test_completed_phases_are_checkpointed_after_each_phase():
    worker = _FakeGuiWorker()
    receipt = await HybridExecutionCoordinator(worker).execute(_task())

    assert receipt["passed"] is True
    assert [write["phase"] for write in worker.checkpoint_writes] == ["prepare", "gui", "verify"]
    assert worker.checkpoint_writes[0]["completed_phases"] == ["prepare"]
    assert worker.checkpoint_writes[-1]["completed_phases"] == ["prepare", "gui", "verify"]
    # The deterministic prepare plan is recorded so a retry can replay it.
    assert worker.checkpoint_writes[0]["prepare_plan"] == [["python3", "-c", "print('ok')"]]


@pytest.mark.asyncio
async def test_recorded_prepare_plan_is_replayed_without_replanning():
    """A retry must reset the artifact with the *same* plan the first attempt used.

    The GUI phase edits the artifact by appending, so a fresh (possibly different)
    prepare plan on retry would corrupt the deliverable.
    """
    worker = _FakeGuiWorker()
    worker.checkpoint = {
        "completed_phases": ["prepare"],
        "protected_paths": ["handoff.txt"],
        "prepare_plan": [["python3", "-c", "print('reset')"]],
    }
    receipt = await HybridExecutionCoordinator(worker).execute(_task())

    assert receipt["passed"] is True
    # The prepare phase replays the recorded command and never asks the model to
    # replan it; only the verify phase's terminal /plan call is issued.
    terminal_plan_calls = [
        path for path in _paths(worker) if path.endswith("/plan") and "/gui/" not in path
    ]
    assert terminal_plan_calls == ["/internal/worker/tasks/T/plan"]
    exec_commands = [
        payload["command"] for _, path, payload in worker.calls if path.endswith("/exec")
    ]
    assert ["python3", "-c", "print('reset')"] in exec_commands
    # The GUI phase still runs so this attempt produces the frame the independent
    # verifier must observe.
    assert len([path for path in _paths(worker) if path.endswith("/gui/execute")]) == 1
    # The protected paths recorded by the prior attempt are re-applied to verify.
    verify_plan = next(
        payload
        for _, path, payload in worker.calls
        if path.endswith("/plan") and "/gui/" not in path
    )
    assert verify_plan["phase"] == "verify"
    assert verify_plan["exclude_paths"] == ["handoff.txt"]


@pytest.mark.asyncio
async def test_desktop_disappearance_mid_task_is_infrastructure_failure():
    worker = _FakeGuiWorker(gui_execute_error=_http_error(409, "Live desktop became unavailable mid-task"))
    receipt = await HybridExecutionCoordinator(worker).execute(_task())

    assert receipt["passed"] is False
    assert receipt["infrastructure_failure"] is True
    # The prepare phase is checkpointed before the GUI phase fails, so a later
    # attempt resumes without rewriting the artifact.
    assert worker.checkpoint_writes[0]["phase"] == "prepare"


@pytest.mark.asyncio
async def test_ordinary_gui_failure_is_not_infrastructure():
    worker = _FakeGuiWorker(gui_execute_error=_http_error(422, "desktop interaction failed"))
    receipt = await HybridExecutionCoordinator(worker).execute(_task())

    assert receipt["passed"] is False
    assert receipt["infrastructure_failure"] is False


@pytest.mark.asyncio
async def test_gui_worker_reports_infrastructure_failure_via_fail_endpoint():
    """A vanished desktop must be reported as a failure, never submitted as a receipt."""
    recorded: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        recorded.append((request.url.path, body))
        if request.url.path.endswith("/start"):
            return httpx.Response(200, json={"status": "running"})
        if request.url.path.endswith("/gui/plan"):
            return httpx.Response(200, json={"steps": [], "expected_text": "hello"})
        if request.url.path.endswith("/gui/execute"):
            return httpx.Response(409, json={"detail": "Live desktop became unavailable mid-task"})
        if request.url.path.endswith("/fail"):
            return httpx.Response(200, json={"status": "retrying", "retry_count": 0})
        if request.url.path.endswith("/submit"):
            raise AssertionError("a vanished desktop must not be submitted as a receipt")
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://core",
        headers={"Authorization": "Bearer " + "t" * 40},
    )
    worker = GuiWorker(
        api_url="http://core", worker_id="gui-worker-test", token="t" * 40, client=client
    )
    try:
        await worker.execute_claim(
            {"task_id": "T", "fencing_token": 1, "execution_mode": "gui"}
        )
    finally:
        await client.aclose()

    paths = [path for path, _ in recorded]
    assert any(path.endswith("/fail") for path in paths)
    assert not any(path.endswith("/submit") for path in paths)


# ---------------------------------------------------------------------------
# Route-level checkpoint persistence (Scope B control-plane contract)
# ---------------------------------------------------------------------------

CHECKPOINT_WORKER_ID = "worker-checkpoint-test"
CHECKPOINT_WORKER_TOKEN = "worker-checkpoint-test-" + "w" * 40


async def _seeded_hybrid_task(async_session, monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{CHECKPOINT_WORKER_ID}":"{CHECKPOINT_WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-CHECKPOINT",
        name="Checkpoint Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
        execution_capabilities=["terminal", "gui"],
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-checkpoint",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/checkpoint-validation",
    )
    await EmployeeRepository(async_session).assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="Checkpoint", objective="Persist hybrid phase progress"
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-CHECKPOINT",
        project_id=project.id,
        assigned_to=employee.id,
        title="Hybrid checkpoint",
        objective="Record completed phases durably",
        scope="Task workspace",
        acceptance_criteria="Checkpoint round-trips",
        execution_mode=ExecutionMode.HYBRID,
        status=TaskStatus.ASSIGNED,
    )
    claim = await task_repo.claim_next_task(
        CHECKPOINT_WORKER_ID, execution_modes=[ExecutionMode.HYBRID]
    )
    assert claim is not None
    await task_repo.start_task_attempt(
        task.task_id,
        CHECKPOINT_WORKER_ID,
        claim["fencing_token"],
        {
            "captured_at_unix": 1790000000.0,
            "files": [],
            "file_count": 0,
            "total_size_bytes": 0,
            "complete": True,
            "errors": [],
        },
    )
    return task, claim


def _checkpoint_client(async_session, manager):
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_checkpoint_manager] = lambda: manager
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
async def test_checkpoint_round_trips_completed_phases(async_session, monkeypatch, tmp_path):
    task, claim = await _seeded_hybrid_task(async_session, monkeypatch, tmp_path)
    manager = CheckpointManager(base_dir=str(tmp_path / "checkpoints"))
    headers = {"Authorization": f"Bearer {CHECKPOINT_WORKER_TOKEN}"}

    async with _checkpoint_client(async_session, manager) as client:
        write = await client.post(
            f"/internal/worker/tasks/{task.task_id}/checkpoint",
            headers=headers,
            json={
                "fencing_token": claim["fencing_token"],
                "phase": "prepare",
                "completed_phases": ["prepare"],
                "protected_paths": ["handoff.txt"],
                "step_index": 1,
            },
        )
        assert write.status_code == 200
        read = await client.get(
            f"/internal/worker/tasks/{task.task_id}/checkpoint",
            headers=headers,
            params={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert read.status_code == 200
    checkpoint = read.json()["checkpoint"]
    assert checkpoint["completed_phases"] == ["prepare"]
    assert checkpoint["protected_paths"] == ["handoff.txt"]


@pytest.mark.asyncio
async def test_checkpoint_from_a_superseded_worker_is_rejected(async_session, monkeypatch, tmp_path):
    task, claim = await _seeded_hybrid_task(async_session, monkeypatch, tmp_path)
    manager = CheckpointManager(base_dir=str(tmp_path / "checkpoints"))
    headers = {"Authorization": f"Bearer {CHECKPOINT_WORKER_TOKEN}"}

    async with _checkpoint_client(async_session, manager) as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/checkpoint",
            headers=headers,
            json={
                "fencing_token": claim["fencing_token"] + 99,
                "phase": "prepare",
                "completed_phases": ["prepare"],
            },
        )
    get_settings.cache_clear()

    assert response.status_code == 409
    assert not manager.has_checkpoint("ANC-EMP-CHECKPOINT", task.task_id)
