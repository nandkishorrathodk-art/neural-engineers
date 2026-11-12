"""Independent verification of GUI/hybrid tasks from live visual evidence.

The GUI worker submits a *claim*: before/after pixel hashes plus per-step receipts.
The independent verifier must re-observe the live desktop itself and only pass when
the frame it captures matches the worker's post-action frame. A worker that never
touched the screen (or fabricated a hash) cannot satisfy that match.
"""

import json

import httpx
import pytest
from neural.workers.verifier import VerificationWorker


def _gui_item(
    *,
    mode: str = "gui",
    worker_hash: str = "a" * 64,
    worker_passed: bool = True,
    planned_commands: list[list[str]] | None = None,
) -> dict:
    receipt: dict = {
        "executor": "gui-worker",
        "execution_mode": mode,
        "gui": {
            "status": "success" if worker_passed else "failed",
            "screen_hash_before": "b" * 64,
            "screen_hash_after": worker_hash,
            "steps_executed": 3,
            "verification": {"passed": worker_passed, "screenshot_hash": worker_hash},
        },
        "verification_status": "awaiting_independent_verifier",
        "passed": worker_passed,
    }
    if planned_commands is not None:
        receipt["planned_commands"] = planned_commands
        receipt["commands"] = [
            {"command": command, "exit_code": 0, "stdout": "ok", "stderr": ""}
            for command in planned_commands
        ]
    return {
        "task_id": "TASK-GUI-1",
        "attempt_id": "attempt-gui-1",
        "fencing_token": 5,
        "execution_mode": mode,
        "acceptance_criteria": "Task completed",
        "workspace_manifest_before": {
            "captured_at_unix": 1789999990.0,
            "files": [],
            "file_count": 0,
            "total_size_bytes": 0,
            "complete": True,
            "errors": [],
        },
        "execution_receipt": receipt,
    }


def _transport(seen, *, live_hash: str, live_captured: bool = True):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/screenshot"):
            return httpx.Response(
                200,
                json={
                    "captured": live_captured,
                    "screenshot_hash": live_hash,
                    "resolution": [1280, 800],
                },
            )
        if request.method == "POST" and request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 0, "stdout": "1 passed", "stderr": ""})
        if request.method == "POST" and request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "completed"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_gui_verification_passes_when_live_hash_matches_worker_frame():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="a" * 64),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_gui_item(worker_hash="a" * 64))
    await verifier.close()

    assert result == {"task_status": "completed"}
    assert [path.rsplit("/", 1)[-1] for path, _ in seen] == ["screenshot", "review"]
    review = seen[-1][1]
    assert review["passed"] is True
    assert review["checks"][0]["passed"] is True
    assert review["evidence_refs"][0] == f"screen:sha256:{'a' * 64}"
    assert review["test_summary"]["independent_screen_capture"]["captured"] is True


@pytest.mark.asyncio
async def test_gui_verification_fails_closed_when_live_hash_differs():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="c" * 64),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_gui_item(worker_hash="a" * 64))
    await verifier.close()

    assert result == {"task_status": "completed"}
    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][0]["passed"] is False


@pytest.mark.asyncio
async def test_gui_verification_fails_closed_when_desktop_unavailable():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="a" * 64, live_captured=False),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_gui_item(worker_hash="a" * 64))
    await verifier.close()

    assert result == {"task_status": "completed"}
    review = seen[-1][1]
    assert review["passed"] is False


@pytest.mark.asyncio
async def test_gui_verification_fails_closed_when_worker_reported_failure():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="a" * 64),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_gui_item(worker_hash="a" * 64, worker_passed=False))
    await verifier.close()

    assert result == {"task_status": "completed"}
    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][1]["passed"] is False


@pytest.mark.asyncio
async def test_hybrid_verification_replays_terminal_phase():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="a" * 64),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(
        _gui_item(mode="hybrid", worker_hash="a" * 64, planned_commands=[["python3", "-m", "unittest", "-v"]])
    )
    await verifier.close()

    assert result == {"task_status": "completed"}
    assert [path.rsplit("/", 1)[-1] for path, _ in seen] == ["screenshot", "exec", "review"]
    review = seen[-1][1]
    assert review["passed"] is True
    assert review["checks"][2]["passed"] is True


@pytest.mark.asyncio
async def test_hybrid_verification_fails_closed_without_replayable_terminal_phase():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="a" * 64),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_gui_item(mode="hybrid", worker_hash="a" * 64, planned_commands=[]))
    await verifier.close()

    assert result == {"task_status": "completed"}
    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][2]["passed"] is False
