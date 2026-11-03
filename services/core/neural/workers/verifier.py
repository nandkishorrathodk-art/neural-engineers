from __future__ import annotations

import asyncio
import os
import re
import signal
from typing import Any

import httpx

TEST_ONLY_ACCEPTANCE = {
    "test passes",
    "tests pass",
    "tests exit successfully",
    "automated tests pass",
    "automated validation passes",
    "pytest passes",
}


def replayable_test_commands(receipt: dict[str, Any]) -> list[list[str]]:
    planned = receipt.get("planned_commands")
    if not isinstance(planned, list):
        return []
    tests: list[list[str]] = []
    for candidate in planned[:12]:
        if not isinstance(candidate, list) or not candidate or not all(isinstance(arg, str) for arg in candidate):
            continue
        command = candidate
        is_pytest = command[0] == "pytest" or (
            command[0] in {"python", "python3"} and command[1:3] == ["-m", "pytest"]
        )
        is_npm_test = command[0] == "npm" and (
            command[1:2] == ["test"] or command[1:3] == ["run", "test"]
        )
        if (is_pytest or is_npm_test) and command not in tests:
            tests.append(command)
    return tests


def worker_test_exit_code(receipt: dict[str, Any], command: list[str]) -> int | None:
    results = receipt.get("commands")
    if not isinstance(results, list):
        return None
    for result in results:
        if isinstance(result, dict) and result.get("command") == command:
            exit_code = result.get("exit_code")
            return exit_code if isinstance(exit_code, int) else None
    return None


def valid_workspace_manifest(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("complete") is not True:
        return False
    files = value.get("files")
    if not isinstance(files, list) or not files or len(files) > 2000:
        return False
    if value.get("file_count") != len(files):
        return False
    total = 0
    for item in files:
        if not isinstance(item, dict):
            return False
        path = item.get("path")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not isinstance(size, int)
            or size < 0
            or size > 10 * 1024 * 1024
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            return False
        total += size
    return total <= 100 * 1024 * 1024 and value.get("total_size_bytes") == total


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

        criteria = str(item.get("acceptance_criteria", "")).lower()
        normalized_criteria = " ".join(re.findall(r"[a-z0-9]+", criteria))
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
                "name": "complete_nonempty_workspace_hash_snapshot_captured",
                "passed": manifest_valid,
                "evidence": {
                    "file_count": manifest.get("file_count") if isinstance(manifest, dict) else None,
                    "complete": manifest.get("complete") if isinstance(manifest, dict) else False,
                    "scope": "verification-time workspace snapshot; no task-start diff",
                },
            },
        ]
        passed = criteria_is_testable and tests_passed and manifest_valid
        notes = (
            "Declared automated tests passed in the worker receipt and an independent verifier rerun. "
            "A complete verification-time workspace hash snapshot was recorded; it does not establish "
            "which files changed during the attempt or whether their contents are correct."
            if passed
            else "Independent verification failed closed: "
            "no replayable test, a test failure, an incomplete/empty workspace hash snapshot, or "
            "acceptance criteria outside the supported test-only contracts."
        )
        evidence_refs = [run["evidence_ref"] for run in test_runs] or [f"verifier:{attempt_id}:no-replayable-test"]
        evidence_refs.extend(manifest_refs)
        return await self._post(
            f"/internal/verifier/tasks/{task_id}/attempts/{attempt_id}/review",
            {
                "passed": passed,
                "notes": notes,
                "evidence_refs": evidence_refs,
                "checks": checks,
                "test_summary": {
                    "independent_test_runs": test_runs,
                    "workspace_manifest": manifest if manifest_valid else {
                        "complete": False,
                        "file_count": len(manifest_files) if isinstance(manifest_files, list) else 0,
                        "errors": manifest.get("errors", []) if isinstance(manifest, dict) else ["manifest unavailable"],
                        "scope": "verification-time workspace snapshot; no task-start diff",
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
            if item.get("execution_mode") != "terminal":
                continue
            try:
                await self.verify_item(item)
                processed += 1
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    raise
            except httpx.HTTPError:
                continue
        return processed

    async def run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except httpx.HTTPError:
                pass
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)
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
