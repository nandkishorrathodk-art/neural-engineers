from types import MethodType

import pytest
from neural.containers import broker_api
from neural.containers.execution_limits import (
    BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS,
    TERMINAL_COMMAND_TIMEOUT_SECONDS,
    WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS,
    command_with_timeout,
)
from neural.containers.manager import ContainerManager
from neural.workers.terminal import TerminalCommandPlan, TerminalWorker


def test_command_timeout_wrapper_bounds_argv_and_shell_commands():
    prefix = [
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=5s",
        f"{TERMINAL_COMMAND_TIMEOUT_SECONDS}s",
    ]

    assert command_with_timeout(["python3", "-c", "print(1)"]) == [
        *prefix,
        "python3",
        "-c",
        "print(1)",
    ]
    assert command_with_timeout("echo hello") == [*prefix, "/bin/sh", "-lc", "echo hello"]
    assert TERMINAL_COMMAND_TIMEOUT_SECONDS < BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS
    assert BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS < WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS


def test_broker_exec_runs_employee_command_under_hard_timeout(monkeypatch):
    class FakeContainer:
        def __init__(self):
            self.exec_kwargs = None

        def exec_run(self, **kwargs):
            self.exec_kwargs = kwargs
            return type("ExecResult", (), {"output": b"done", "exit_code": 0})()

    container = FakeContainer()
    monkeypatch.setattr(broker_api, "validate_container_id", lambda _container_id: None)
    monkeypatch.setattr(broker_api, "get_managed_container", lambda _container_id: container)

    result = broker_api.execute_in_container(
        "ancos-desktop-anc-emp-timeout",
        broker_api.ExecRequest(command=["python3", "-c", "print('done')"]),
    )

    assert result == {"exit_code": 0, "stdout": "done", "stderr": ""}
    assert container.exec_kwargs["cmd"] == command_with_timeout(["python3", "-c", "print('done')"])
    assert container.exec_kwargs["user"] == "1000:1000"


@pytest.mark.asyncio
async def test_container_manager_waits_past_broker_command_deadline(monkeypatch):
    manager = ContainerManager()
    manager.settings.DOCKER_BROKER_URL = "http://broker.internal"
    manager.settings.DOCKER_BROKER_TOKEN = "b" * 40
    seen = {}

    async def fake_broker_request(_method, _path, **kwargs):
        seen.update(kwargs)
        return {"exit_code": 0, "stdout": "done", "stderr": ""}

    monkeypatch.setattr(manager, "_broker_request", fake_broker_request)

    result = await manager.execute_command(
        "ancos-desktop-anc-emp-timeout",
        ["python3", "-c", "print('done')"],
    )

    assert result["exit_code"] == 0
    assert seen["timeout"] == float(BROKER_COMMAND_REQUEST_TIMEOUT_SECONDS)
    assert seen["json"]["command"] == ["python3", "-c", "print('done')"]


@pytest.mark.asyncio
async def test_terminal_worker_exec_request_timeout_exceeds_broker_limit():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"exit_code": 0, "stdout": "done", "stderr": ""}

    class FakeClient:
        def __init__(self):
            self.requests = []

        async def post(self, path, json, **kwargs):
            self.requests.append((path, json, kwargs))
            return FakeResponse()

        async def aclose(self):
            return None

    worker = TerminalWorker("http://core.internal", "terminal-worker", "w" * 40)
    fake_client = FakeClient()
    worker.client = fake_client

    async def plan_commands(_self, _task):
        return TerminalCommandPlan(commands=[["python3", "-c", "print(1)"]]), {"model": "test"}

    worker._plan_commands = MethodType(plan_commands, worker)
    try:
        await worker.execute_claim(
            {
                "task_id": "TASK-TIMEOUT",
                "fencing_token": 1,
                "resource_budget": {"max_tokens": 100},
            }
        )
    finally:
        await worker.close()

    exec_request = next(request for request in fake_client.requests if request[0].endswith("/exec"))
    assert exec_request[2]["timeout"].read == WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS
