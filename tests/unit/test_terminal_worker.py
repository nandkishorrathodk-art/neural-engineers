from types import SimpleNamespace

import httpx
import pytest
from neural.workers.terminal import TerminalCommandPlan, TerminalWorker


class StubLLM:
    async def generate_structured(self, **_kwargs):
        return (
            TerminalCommandPlan(commands=[["pytest", "-q"]]),
            SimpleNamespace(
                provider="test-provider",
                model="test-model",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
            ),
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
        llm=StubLLM(),  # type: ignore[arg-type]
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
        "/internal/worker/tasks/TASK-1/exec",
        "/internal/worker/tasks/TASK-1/submit",
    ]
    receipt = seen[-1][1]["execution_receipt"]
    assert receipt["verification_status"] == "awaiting_independent_verifier"
    assert receipt["passed"] is False


def test_terminal_command_plan_rejects_shell_and_non_allowlisted_commands():
    with pytest.raises(ValueError):
        TerminalCommandPlan(commands=[["sh", "-c", "id"]])
    with pytest.raises(ValueError):
        TerminalCommandPlan(commands=[["curl", "https://example.invalid"]])
