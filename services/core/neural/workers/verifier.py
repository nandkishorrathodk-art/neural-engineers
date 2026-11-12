from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import uuid
from typing import Any

import httpx

from neural.verification.test_commands import (
    TEST_ONLY_ACCEPTANCE,
    normalize_acceptance_criteria,
    replayable_test_commands,
)
from neural.verification.workspace_manifest import build_workspace_delta, valid_workspace_manifest


def worker_test_exit_code(receipt: dict[str, Any], command: list[str]) -> int | None:
    results = receipt.get("commands")
    if not isinstance(results, list):
        return None
    for result in results:
        if isinstance(result, dict) and result.get("command") == command:
            exit_code = result.get("exit_code")
            return exit_code if isinstance(exit_code, int) else None
    return None


class VerificationWorker:
    """Re-run only declared test commands under a separate verifier identity."""

    def __init__(
        self,
        api_url: str,
        verifier_id: str,
        token: str,
        poll_interval: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_url or not verifier_id or len(token) < 32:
            raise ValueError("Core API URL, verifier identity, and a 32-character verifier token are required.")
        self.verifier_id = verifier_id
        self.instance_id = uuid.uuid4().hex
        self.client = client or httpx.AsyncClient(
            base_url=api_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(330.0, connect=5.0),
        )
        self.poll_interval = max(0.5, min(poll_interval, 60.0))
        self._stopping = asyncio.Event()

    async def close(self) -> None:
        self._stopping.set()
        await self.client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(path, json=payload)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Core API returned an invalid verifier response.")
        return result

    async def verify_item(self, item: dict[str, Any]) -> dict[str, Any] | None:
        task_id = str(item["task_id"])
        attempt_id = str(item["attempt_id"])
        fence = int(item["fencing_token"])
        execution_receipt = item.get("execution_receipt")
        if not isinstance(execution_receipt, dict):
            execution_receipt = {}
        mode = str(item.get("execution_mode", "terminal")).lower()
        if mode in {"gui", "gui_only", "hybrid"}:
            return await self.verify_gui_item(item, execution_receipt)
        workspace_manifest_before = item.get("workspace_manifest_before")
        baseline_valid = valid_workspace_manifest(workspace_manifest_before, allow_empty=True)

        commands = replayable_test_commands(execution_receipt)
        test_runs: list[dict[str, Any]] = []
        for index, command in enumerate(commands, start=1):
            result = await self._post(
                f"/internal/verifier/tasks/{task_id}/attempts/{attempt_id}/exec",
                {"fencing_token": fence, "command": command},
            )
            test_runs.append(
                {
                    "command": command,
                    "worker_exit_code": worker_test_exit_code(execution_receipt, command),
                    "verifier_exit_code": result.get("exit_code"),
                    "stdout": str(result.get("stdout", ""))[-8000:],
                    "stderr": str(result.get("stderr", ""))[-2000:],
                    "evidence_ref": f"verifier:{attempt_id}:test:{index}",
                }
            )

        try:
            manifest = await self._post(
                f"/internal/verifier/tasks/{task_id}/attempts/{attempt_id}/manifest",
                {"fencing_token": fence},
            )
        except httpx.HTTPError:
            manifest = {}
        manifest_valid = valid_workspace_manifest(manifest)
        manifest_files = manifest.get("files", []) if isinstance(manifest, dict) else []
        manifest_refs = [f"workspace:sha256:{entry['sha256']}" for entry in manifest_files] if manifest_valid else []
        workspace_delta = (
            build_workspace_delta(workspace_manifest_before, manifest)
            if baseline_valid and manifest_valid
            else None
        )

        normalized_criteria = normalize_acceptance_criteria(item.get("acceptance_criteria"))
        criteria_is_testable = normalized_criteria in TEST_ONLY_ACCEPTANCE
        tests_passed = bool(test_runs) and all(
            run["worker_exit_code"] == 0 and run["verifier_exit_code"] == 0 for run in test_runs
        )
        checks = [
            {
                "name": "acceptance_criteria_is_supported_test_only_contract",
                "passed": criteria_is_testable,
                "evidence": item.get("acceptance_criteria", ""),
            },
            {
                "name": "worker_test_result_and_independent_rerun_passed",
                "passed": tests_passed,
                "evidence": [
                    {
                        "command": run["command"],
                        "worker_exit_code": run["worker_exit_code"],
                        "verifier_exit_code": run["verifier_exit_code"],
                    }
                    for run in test_runs
                ],
            },
            {
                "name": "complete_task_start_workspace_baseline_captured",
                "passed": baseline_valid,
                "evidence": {
                    "file_count": workspace_manifest_before.get("file_count") if baseline_valid else None,
                    "captured_at_unix": workspace_manifest_before.get("captured_at_unix") if baseline_valid else None,
                    "complete": baseline_valid,
                },
            },
            {
                "name": "complete_verification_snapshot_and_workspace_delta_captured",
                "passed": manifest_valid and workspace_delta is not None,
                "evidence": {
                    "file_count": manifest.get("file_count") if isinstance(manifest, dict) else None,
                    "complete": manifest_valid,
                    "delta_counts": workspace_delta["counts"] if workspace_delta else None,
                    "scope": "path and SHA-256 delta; file contents are not included",
                },
            },
        ]
        passed = criteria_is_testable and tests_passed and baseline_valid and manifest_valid and workspace_delta is not None
        notes = (
            "Declared automated tests passed in the worker receipt and an independent verifier rerun. "
            "Complete task-start and verification-time workspace hash snapshots were recorded with a "
            "path-level delta. Hash changes do not establish whether the changed file contents are correct."
            if passed
            else "Independent verification failed closed: "
            "no replayable test, a test failure, a missing/invalid task-start baseline, an incomplete/empty "
            "verification snapshot, or acceptance criteria outside the supported test-only contracts."
        )
        evidence_refs = [run["evidence_ref"] for run in test_runs] or [f"verifier:{attempt_id}:no-replayable-test"]
        evidence_refs.extend(manifest_refs)
        if baseline_valid:
            baseline_digest = hashlib.sha256(
                json.dumps(workspace_manifest_before, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            evidence_refs.append(f"workspace-baseline:sha256:{baseline_digest}")
        if workspace_delta:
            delta_digest = hashlib.sha256(
                json.dumps(workspace_delta, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            evidence_refs.append(f"workspace-delta:sha256:{delta_digest}")
        return await self._post(
            f"/internal/verifier/tasks/{task_id}/attempts/{attempt_id}/review",
            {
                "passed": passed,
                "notes": notes,
                "evidence_refs": evidence_refs,
                "checks": checks,
                "test_summary": {
                    "independent_test_runs": test_runs,
                    "workspace_manifest_before": workspace_manifest_before if baseline_valid else {
                        "complete": False,
                        "file_count": 0,
                        "errors": ["task-start workspace baseline missing or invalid"],
                    },
                    "workspace_manifest": manifest if manifest_valid else {
                        "complete": False,
                        "file_count": len(manifest_files) if isinstance(manifest_files, list) else 0,
                        "errors": manifest.get("errors", []) if isinstance(manifest, dict) else ["manifest unavailable"],
                        "scope": "verification-time workspace snapshot",
                    },
                    "workspace_delta": workspace_delta or {
                        "complete": False,
                        "errors": ["task-start baseline or verification snapshot is unavailable"],
                    },
                },
            },
        )

    async def verify_gui_item(self, item: dict[str, Any], execution_receipt: dict[str, Any]) -> dict[str, Any] | None:
        """Independently verify a GUI or hybrid task from live visual evidence.

        The worker's receipt is a *claim*: it reports before/after pixel hashes and
        per-step receipts. Independent verification re-observes the same live desktop
        itself, requires the worker's post-action frame hash to match the frame the
        verifier captures, and re-runs the worker's declared terminal verification
        commands (hybrid tasks) against the workspace. A worker that never touched the
        screen, or that fabricated a hash, cannot satisfy the live-hash match.
        """
        task_id = str(item["task_id"])
        attempt_id = str(item["attempt_id"])
        fence = int(item["fencing_token"])
        gui_receipt = execution_receipt.get("gui")
        if not isinstance(gui_receipt, dict):
            gui_receipt = {}
        verification = gui_receipt.get("verification") if isinstance(gui_receipt.get("verification"), dict) else {}
        worker_hash = gui_receipt.get("screen_hash_after")
        if not isinstance(worker_hash, str) or not worker_hash:
            worker_hash = verification.get("screenshot_hash")
        worker_passed = execution_receipt.get("passed") is True

        checks: list[dict[str, Any]] = []
        evidence_refs: list[str] = []

        # 1. Independent live desktop observation (never trust the worker's hash).
        live: dict[str, Any] = {}
        try:
            live = await self._post(
                f"/internal/verifier/tasks/{task_id}/attempts/{attempt_id}/screenshot",
                {"fencing_token": fence},
            )
        except httpx.HTTPError:
            live = {}
        live_captured = bool(live.get("captured"))
        live_hash = str(live.get("screenshot_hash") or "")
        hash_matches = bool(live_hash) and live_hash == worker_hash
        checks.append(
            {
                "name": "independent_live_desktop_hash_matches_worker_post_action_frame",
                "passed": live_captured and hash_matches,
                "evidence": {
                    "live_captured": live_captured,
                    "verifier_screenshot_hash": live_hash,
                    "worker_screenshot_hash": worker_hash,
                    "resolution": live.get("resolution"),
                },
            }
        )
        if live_hash:
            evidence_refs.append(f"screen:sha256:{live_hash}")

        # 2. The worker itself must have judged the GUI loop successful.
        checks.append(
            {
                "name": "worker_gui_loop_reported_success",
                "passed": worker_passed,
                "evidence": {"worker_passed": worker_passed, "status": gui_receipt.get("status")},
            }
        )

        # 3. Hybrid tasks must also expose a replayable terminal verification phase.
        hybrid = str(item.get("execution_mode", "")).lower() == "hybrid"
        tests_passed = True
        if hybrid:
            commands = replayable_test_commands(execution_receipt)
            if commands:
                runs: list[dict[str, Any]] = []
                for index, command in enumerate(commands, start=1):
                    result = await self._post(
                        f"/internal/verifier/tasks/{task_id}/attempts/{attempt_id}/exec",
                        {"fencing_token": fence, "command": command},
                    )
                    runs.append(
                        {
                            "command": command,
                            "worker_exit_code": worker_test_exit_code(execution_receipt, command),
                            "verifier_exit_code": result.get("exit_code"),
                        }
                    )
                    evidence_refs.append(f"verifier:{attempt_id}:test:{index}")
                tests_passed = all(
                    run["worker_exit_code"] == 0 and run["verifier_exit_code"] == 0 for run in runs
                )
            else:
                tests_passed = False
            checks.append(
                {
                    "name": "hybrid_terminal_phase_independently_replayable",
                    "passed": tests_passed,
                    "evidence": {"replayable_commands": len(commands)},
                }
            )

        passed = live_captured and hash_matches and worker_passed and tests_passed
        notes = (
            "Independent visual verification passed: the verifier observed the live employee desktop "
            "itself and the captured frame hash matched the worker's post-action frame, and the worker's "
            "GUI loop reported success."
            + (" The hybrid terminal verification phase was independently replayed." if hybrid else "")
            if passed
            else "Independent visual verification failed closed: the live desktop could not be observed, "
            "the independently captured frame did not match the worker's post-action frame, the worker's "
            "GUI loop did not report success, or a hybrid terminal phase was not independently replayable."
        )
        if not evidence_refs:
            evidence_refs = [f"verifier:{attempt_id}:no-visual-evidence"]
        return await self._post(
            f"/internal/verifier/tasks/{task_id}/attempts/{attempt_id}/review",
            {
                "passed": passed,
                "notes": notes,
                "evidence_refs": evidence_refs,
                "checks": checks,
                "test_summary": {
                    "independent_screen_capture": live or {"captured": False},
                    "worker_visual_receipt": {
                        "screen_hash_before": gui_receipt.get("screen_hash_before"),
                        "screen_hash_after": worker_hash,
                        "steps_executed": gui_receipt.get("steps_executed"),
                        "verification": verification,
                    },
                },
            },
        )

    async def run_once(self) -> int:
        response = await self.client.get("/internal/verifier/queue", params={"limit": 50})
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else []
        processed = 0
        for item in items:
            if self._stopping.is_set():
                break
            if not isinstance(item, dict):
                continue
            if str(item.get("execution_mode", "")).lower() not in {"terminal", "terminal_only", "gui", "gui_only", "hybrid"}:
                continue
            try:
                await self.verify_item(item)
                processed += 1
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (409, 503):
                    raise
            except httpx.HTTPError:
                continue
        return processed

    async def run(self) -> None:
        heartbeat_task = asyncio.create_task(self._runtime_heartbeat_loop())
        try:
            while not self._stopping.is_set():
                try:
                    await self.run_once()
                except httpx.HTTPError:
                    pass
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    continue
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _runtime_heartbeat_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._post(
                    "/internal/verifier/runtime/heartbeat",
                    {"instance_id": self.instance_id},
                )
            except httpx.HTTPError:
                pass
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=20.0)
            except TimeoutError:
                continue


async def main() -> None:
    worker = VerificationWorker(
        api_url=os.environ.get("ANCOS_CORE_API_URL", "http://127.0.0.1:12000"),
        verifier_id=os.environ.get("ANCOS_VERIFIER_ID", "independent-verifier"),
        token=os.environ.get("VERIFIER_API_TOKEN", ""),
        poll_interval=float(os.environ.get("ANCOS_VERIFIER_POLL_SECONDS", "5")),
    )
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(stop_signal, worker._stopping.set)
        except NotImplementedError:
            pass
    try:
        await worker.run()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
