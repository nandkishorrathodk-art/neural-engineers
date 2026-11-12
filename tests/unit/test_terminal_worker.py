import httpx
import pytest
from neural.workers.terminal import (
    TerminalCommandPlan,
    TerminalPlan,
    TerminalWorker,
    WorkspaceFile,
    _write_commands,
)


@pytest.mark.asyncio
async def test_terminal_worker_submits_receipt_after_zero_exit_commands():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = await request.aread()
        import json

        body = json.loads(payload) if payload else {}
        seen.append((request.url.path, body))
        if request.url.path.endswith("/start"):
            result = {"status": "running"}
        elif request.url.path.endswith("/plan"):
            result = {
                "plan": {"commands": [["pytest", "-q"]]},
                "model": {
                    "provider": "test-provider",
                    "model": "test-model",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cost_usd": 0.001,
                },
            }
        elif request.url.path.endswith("/exec"):
            result = {"exit_code": 0, "stdout": "1 passed", "stderr": ""}
        elif request.url.path.endswith("/submit"):
            result = {"status": "submitted_for_verification"}
        else:
            return httpx.Response(404)
        return httpx.Response(200, json=result)

    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=httpx.MockTransport(handler),
    )
    worker = TerminalWorker(
        api_url="http://core.test",
        worker_id="worker-test",
        token="w" * 32,
        client=client,
    )
    await worker.execute_claim({
        "task_id": "TASK-1",
        "project_id": "PROJECT-1",
        "employee_id": "ANC-EMP-1",
        "fencing_token": 4,
        "title": "Run tests",
        "objective": "Run unit tests",
        "scope": "Run tests in workspace",
        "acceptance_criteria": "Tests exit successfully",
        "resource_budget": {"max_tokens": 1000},
    })
    await worker.close()

    paths = [path for path, _ in seen]
    assert paths == [
        "/internal/worker/tasks/TASK-1/start",
        "/internal/worker/tasks/TASK-1/plan",
        "/internal/worker/tasks/TASK-1/exec",
        "/internal/worker/tasks/TASK-1/submit",
    ]
    receipt = seen[-1][1]["execution_receipt"]
    assert receipt["verification_status"] == "awaiting_independent_verifier"
    assert receipt["passed"] is False
    assert receipt["planned_commands"] == [["pytest", "-q"]]
    assert receipt["commands"][0]["command"] == ["pytest", "-q"]


@pytest.mark.asyncio
async def test_terminal_worker_reports_runtime_heartbeat():
    seen: list[tuple[str, dict]] = []
    worker: TerminalWorker

    async def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(await request.aread())
        seen.append((request.url.path, body))
        worker._stopping.set()
        return httpx.Response(200, json={"status": "recorded"})

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    worker = TerminalWorker(
        api_url="http://core.test",
        worker_id="worker-test",
        token="w" * 32,
        client=client,
    )

    await worker._runtime_heartbeat_loop()
    await worker.close()

    assert seen == [(
        "/internal/worker/runtime/heartbeat",
        {"instance_id": worker.instance_id},
    )]


def test_terminal_command_plan_rejects_shell_and_non_allowlisted_commands():
    with pytest.raises(ValueError):
        TerminalCommandPlan(commands=[["sh", "-c", "id"]])
    with pytest.raises(ValueError):
        TerminalCommandPlan(commands=[["curl", "https://example.invalid"]])


def test_terminal_command_plan_normalizes_plain_strings_to_argv_without_shell():
    plan = TerminalCommandPlan(commands=[
        "python3 -c \"from pathlib import Path; Path('result.py').write_text('value = 1')\"",
        "python3 -m unittest -v",
    ])

    assert plan.commands == [
        ["python3", "-c", "from pathlib import Path; Path('result.py').write_text('value = 1')"],
        ["python3", "-m", "unittest", "-v"],
    ]


def test_terminal_command_plan_rejects_shell_command_strings_after_tokenization():
    with pytest.raises(ValueError):
        TerminalCommandPlan(commands=["echo hello > result.txt"])


def test_terminal_plan_materializes_files_as_persisted_argv_writes():
    plan = TerminalPlan(
        files=[WorkspaceFile(path="test_clamp.py", content="value = 'a\\nb'\n")],
        commands=[["python3", "-m", "unittest", "-v"]],
    ).materialize()

    assert plan.commands[-1] == ["python3", "-m", "unittest", "-v"]
    writes = plan.commands[:-1]
    assert writes and all(command[0] == "python3" and command[1] == "-c" for command in writes)

    # Replaying the writes reproduces the exact file contents, byte for byte.
    import pathlib
    import tempfile

    with tempfile.TemporaryDirectory() as workspace:
        for command in writes:
            script = command[2].replace("'test_clamp.py'", repr(f"{workspace}/test_clamp.py"))
            exec(script)
        assert pathlib.Path(workspace, "test_clamp.py").read_text() == "value = 'a\\nb'\n"


def test_terminal_plan_materializes_large_files_in_bounded_chunks():
    content = "x" * 20_000
    writes = _write_commands("big.txt", content)

    assert len(writes) > 1
    assert all(len(command[2]) <= 8192 for command in writes)


def test_terminal_plan_rejects_files_outside_the_workspace():
    with pytest.raises(ValueError):
        WorkspaceFile(path="../escape.py", content="value = 1")
    with pytest.raises(ValueError):
        WorkspaceFile(path="/etc/passwd", content="value = 1")
