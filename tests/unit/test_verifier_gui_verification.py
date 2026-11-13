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
    worker_phash: str | None = None,
    worker_passed: bool = True,
    planned_commands: list[list[str]] | None = None,
    acceptance_criteria: str = "Task completed",
    objective: str | None = None,
    title: str | None = None,
) -> dict:
    gui_receipt: dict = {
        "status": "success" if worker_passed else "failed",
        "screen_hash_before": "b" * 64,
        "screen_hash_after": worker_hash,
        "steps_executed": 3,
        "verification": {"passed": worker_passed, "screenshot_hash": worker_hash},
    }
    if worker_phash is not None:
        gui_receipt["screen_phash_after"] = worker_phash
    receipt: dict = {
        "executor": "gui-worker",
        "execution_mode": mode,
        "gui": gui_receipt,
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
        "acceptance_criteria": acceptance_criteria,
        "objective": objective,
        "title": title,
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


def _transport(
    seen,
    *,
    live_hash: str,
    live_captured: bool = True,
    live_phash: str | None = None,
    observed_terms: list[str] | None = None,
    ocr_available: bool = True,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/screenshot"):
            payload = {
                "captured": live_captured,
                "screenshot_hash": live_hash,
                "resolution": [1280, 800],
                "expected_terms": body.get("expected_terms", []),
                "observed_terms": observed_terms
                if observed_terms is not None
                else body.get("expected_terms", []),
                "ocr_available": ocr_available,
            }
            if live_phash is not None:
                payload["screenshot_phash"] = live_phash
            return httpx.Response(200, json=payload)
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
    assert review["checks"][0]["evidence"]["match_method"] == "exact"
    assert review["evidence_refs"][0] == f"screen:sha256:{'a' * 64}"
    assert review["test_summary"]["independent_screen_capture"]["captured"] is True


@pytest.mark.asyncio
async def test_gui_verification_passes_on_perceptual_match_when_pixels_differ():
    """A live desktop is never byte-identical (blinking caret, ticking clock), so a
    small whole-screen dHash distance must still verify instead of failing closed."""
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(
            seen,
            live_hash="c" * 64,
            live_phash="00000000000000ff",
        ),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(
        _gui_item(worker_hash="a" * 64, worker_phash="0000000000000000")
    )
    await verifier.close()

    assert result == {"task_status": "completed"}
    review = seen[-1][1]
    assert review["passed"] is True
    assert review["checks"][0]["passed"] is True
    assert review["checks"][0]["evidence"]["match_method"] == "perceptual"


@pytest.mark.asyncio
async def test_gui_verification_fails_closed_when_perceptual_hashes_diverge():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="c" * 64, live_phash="ffffffffffffffff"),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(
        _gui_item(worker_hash="a" * 64, worker_phash="0000000000000000")
    )
    await verifier.close()

    assert result == {"task_status": "completed"}
    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][0]["passed"] is False
    assert review["checks"][0]["evidence"]["match_method"] == "mismatch"


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
        _gui_item(
            mode="hybrid",
            worker_hash="a" * 64,
            planned_commands=[["python3", "-m", "unittest", "-v"]],
        )
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
    result = await verifier.verify_item(
        _gui_item(mode="hybrid", worker_hash="a" * 64, planned_commands=[])
    )
    await verifier.close()

    assert result == {"task_status": "completed"}
    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][2]["passed"] is False


@pytest.mark.asyncio
async def test_gui_verification_requires_verifier_to_read_onscreen_deliverable():
    """A similar-looking screen that lacks the required marker must be rejected, even
    when the live frame matches the worker's frame and the worker reported success."""
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="a" * 64, observed_terms=[]),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(
        _gui_item(
            worker_hash="a" * 64,
            acceptance_criteria="The text editor window is open and the text GUI-OK appears.",
        )
    )
    await verifier.close()

    assert result == {"task_status": "completed"}
    # The verifier asked the core API to OCR the marker it extracted from the contract.
    screenshot_body = seen[0][1]
    assert screenshot_body["expected_terms"] == ["GUI-OK"]
    review = seen[-1][1]
    assert review["passed"] is False
    terms_check = next(
        check
        for check in review["checks"]
        if check["name"] == "onscreen_deliverable_terms_observed_by_verifier"
    )
    assert terms_check["passed"] is False
    assert terms_check["evidence"]["missing_terms"] == ["GUI-OK"]


@pytest.mark.asyncio
async def test_gui_verification_passes_when_verifier_reads_onscreen_deliverable():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="a" * 64, observed_terms=["GUI-OK"]),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(
        _gui_item(
            worker_hash="a" * 64,
            acceptance_criteria="The text editor window is open and the text GUI-OK appears.",
        )
    )
    await verifier.close()

    assert result == {"task_status": "completed"}
    review = seen[-1][1]
    assert review["passed"] is True
    terms_check = next(
        check
        for check in review["checks"]
        if check["name"] == "onscreen_deliverable_terms_observed_by_verifier"
    )
    assert terms_check["passed"] is True
    assert terms_check["evidence"]["observed_terms"] == ["GUI-OK"]


@pytest.mark.asyncio
async def test_gui_verification_fails_closed_when_required_marker_terms_but_ocr_unavailable():
    seen: list[tuple[str, dict]] = []
    client = httpx.AsyncClient(
        base_url="http://core.test",
        transport=_transport(seen, live_hash="a" * 64, observed_terms=[], ocr_available=False),
    )
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    await verifier.verify_item(
        _gui_item(
            worker_hash="a" * 64, acceptance_criteria="Document contains the text 'HYBRID-READY'."
        )
    )
    await verifier.close()

    review = seen[-1][1]
    assert review["passed"] is False


def test_onscreen_expected_terms_extracts_markers_and_quoted_text():
    from neural.workers.verifier import onscreen_expected_terms

    terms = onscreen_expected_terms(
        {
            "title": "Open text editor and type HYBRID-READY",
            "objective": "Type the marker HYBRID-READY into a new document.",
            "acceptance_criteria": "Document contains text 'HYBRID-READY' and file is saved.",
        }
    )
    assert "HYBRID-READY" in terms
    assert terms.count("HYBRID-READY") == 1


def test_onscreen_expected_terms_empty_for_generic_contract():
    from neural.workers.verifier import onscreen_expected_terms

    assert onscreen_expected_terms({"acceptance_criteria": "Task completed"}) == []
