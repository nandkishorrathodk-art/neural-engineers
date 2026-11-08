from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from neural.verification.test_commands import TEST_ONLY_ACCEPTANCE, normalize_acceptance_criteria

SIMULATION_MARKERS = {"mock", "stub", "simulation", "simulated", "fallback", "offline"}


class AcceptanceBlocked(Exception):
    """A required live dependency or disposable-runtime precondition is absent."""


class AcceptanceFailed(Exception):
    """A real acceptance operation ran and did not satisfy its evidence contract."""


@dataclass
class AcceptanceReport:
    status: str
    run_id: str
    api_url: str
    started_at_unix: float
    finished_at_unix: float | None = None
    project_id: str | None = None
    project_name: str | None = None
    employee_id: str | None = None
    workstation_id: str | None = None
    task_results: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    cleanup: dict[str, Any] = field(default_factory=dict)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AcceptanceBlocked(f"Required environment variable {name} is not configured.")
    return value


def _bounded_env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise AcceptanceBlocked(f"{name} must be an integer between {low} and {high}.") from exc
    return max(low, min(value, high))


def _normalize_acceptance(value: Any) -> str:
    return normalize_acceptance_criteria(value)


def _real_model_receipt(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceFailed(f"{context} has no persisted model identity receipt.")
    identity = " ".join(str(value.get(key, "")) for key in ("provider", "model")).strip().lower()
    if not value.get("provider") or not value.get("model"):
        raise AcceptanceFailed(f"{context} model receipt is missing provider or model identity.")
    if any(marker in identity for marker in SIMULATION_MARKERS):
        raise AcceptanceFailed(f"{context} used a simulated, fallback, or stub model route.")
    return value


def _real_model_name(value: Any, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceFailed(f"{context} has no persisted model identity.")
    normalized = value.strip().lower()
    if any(marker in normalized for marker in SIMULATION_MARKERS):
        raise AcceptanceFailed(f"{context} used a simulated, fallback, or stub model route.")
    return value


def validate_terminal_acceptance_plan(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Require one atomic test-bearing task for this focused live runner."""
    if len(tasks) != 1:
        raise AcceptanceFailed(
            "The focused acceptance mission requires one task that creates both source and tests before running them."
        )
    unsupported = [
        task.get("task_id", "<unknown>") for task in tasks
        if str(task.get("execution_mode", "")).lower() != "terminal"
        or _normalize_acceptance(task.get("acceptance_criteria")) not in TEST_ONLY_ACCEPTANCE
    ]
    if unsupported:
        raise AcceptanceFailed(
            "Boss plan contains tasks outside terminal mode or the independent verifier's test-only allowlist: "
            + ", ".join(str(task_id) for task_id in unsupported)
        )
    return tasks


def validate_verified_task(task: dict[str, Any]) -> dict[str, Any]:
    """Validate allowlisted, persisted independent evidence from one owner-report task."""
    if str(task.get("status", "")).lower() != "completed" or task.get("verified") is not True:
        raise AcceptanceFailed(f"Task {task.get('task_id', '<unknown>')} is not independently verified.")
    if str(task.get("execution_mode", "")).lower() != "terminal":
        raise AcceptanceFailed(f"Task {task.get('task_id', '<unknown>')} did not use terminal execution.")
    if _normalize_acceptance(task.get("acceptance_criteria")) not in TEST_ONLY_ACCEPTANCE:
        raise AcceptanceFailed(
            f"Task {task.get('task_id', '<unknown>')} used acceptance criteria outside the verifier's exact test-only allowlist."
        )

    attempts = task.get("attempts")
    if not isinstance(attempts, list):
        raise AcceptanceFailed("Owner project report did not include task attempt evidence.")
    passed_attempts = [
        attempt for attempt in attempts
        if isinstance(attempt, dict)
        and attempt.get("verification_passed") is True
        and attempt.get("status") == "verified"
    ]
    if not passed_attempts:
        raise AcceptanceFailed(f"Task {task.get('task_id')} has no persisted passing verifier attempt.")
    verifier_id = passed_attempts[-1].get("verifier_id")
    if not verifier_id or verifier_id in {task.get("assigned_employee_id"), passed_attempts[-1].get("worker_id")}:
        raise AcceptanceFailed(f"Task {task.get('task_id')} lacks a separate verifier identity.")
    _real_model_receipt(passed_attempts[-1].get("execution_model"), context=f"Task {task.get('task_id')} worker")

    evidence = task.get("verification_evidence")
    if not isinstance(evidence, dict) or evidence.get("passed") is not True:
        raise AcceptanceFailed(f"Task {task.get('task_id')} has no owner-visible verifier receipt.")
    checks = evidence.get("checks")
    required_checks = {
        "acceptance_criteria_is_supported_test_only_contract",
        "worker_test_result_and_independent_rerun_passed",
        "complete_task_start_workspace_baseline_captured",
        "complete_verification_snapshot_and_workspace_delta_captured",
    }
    if not isinstance(checks, list) or not required_checks.issubset(
        {check.get("name") for check in checks if isinstance(check, dict) and check.get("passed") is True}
    ):
        raise AcceptanceFailed(f"Task {task.get('task_id')} verifier checks are missing or not all passing.")

    test_runs = evidence.get("independent_test_runs")
    if not isinstance(test_runs, list) or not test_runs:
        raise AcceptanceFailed(f"Task {task.get('task_id')} has no independently replayed test command.")
    if not all(
        isinstance(run, dict)
        and run.get("worker_exit_code") == 0
        and run.get("verifier_exit_code") == 0
        and run.get("executable") in {"python", "python3", "pytest", "npm"}
        and run.get("evidence_ref")
        for run in test_runs
    ):
        raise AcceptanceFailed(f"Task {task.get('task_id')} has a failed or incomplete independent test replay.")

    baseline = evidence.get("workspace_manifest_before")
    manifest = evidence.get("workspace_manifest")
    delta = evidence.get("workspace_delta")
    if not isinstance(baseline, dict) or baseline.get("complete") is not True:
        raise AcceptanceFailed(f"Task {task.get('task_id')} has no complete task-start workspace baseline.")
    if type(baseline.get("file_count")) is not int or baseline["file_count"] != 0:
        raise AcceptanceFailed(f"Task {task.get('task_id')} did not start with the required empty employee workspace.")
    if (
        not isinstance(manifest, dict)
        or manifest.get("complete") is not True
        or not isinstance(manifest.get("files"), list)
        or type(manifest.get("file_count")) is not int
        or manifest.get("file_count", 0) <= 0
        or type(manifest.get("returned_file_count")) is not int
        or manifest.get("returned_file_count") != len(manifest["files"])
        or manifest["file_count"] < manifest["returned_file_count"]
        or not manifest["files"]
    ):
        raise AcceptanceFailed(f"Task {task.get('task_id')} has no complete nonempty artifact manifest.")
    if not isinstance(delta, dict) or delta.get("complete") is not True:
        raise AcceptanceFailed(f"Task {task.get('task_id')} has no complete workspace delta.")
    raw_created = delta.get("created", [])
    raw_modified = delta.get("modified", [])
    created: list[Any] = raw_created if isinstance(raw_created, list) else []
    modified: list[Any] = raw_modified if isinstance(raw_modified, list) else []
    changed_hashes: dict[str, str] = {}
    for item in created:
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
            changed_hashes[item["path"]] = item["sha256"]
    for item in modified:
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("after_sha256"), str):
            changed_hashes[item["path"]] = item["after_sha256"]
    if not changed_hashes:
        raise AcceptanceFailed(f"Task {task.get('task_id')} did not create or modify a workspace artifact.")
    # The focused mission intentionally asks for one combined source-and-test
    # file (`test_clamp.py`). Requiring separate implementation and test files
    # here would contradict the owner's acceptance objective and reject every
    # otherwise-valid receipt for that mission.
    if set(changed_hashes) != {"test_clamp.py"}:
        raise AcceptanceFailed(
            f"Task {task.get('task_id')} did not produce exactly the requested root-level test_clamp.py artifact."
        )
    for item in manifest["files"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not item["path"]
            or len(item["path"]) > 256
            or item["path"].startswith("/")
            or "\\" in item["path"]
            or any(part in {"", ".", ".."} for part in item["path"].split("/"))
            or type(item.get("size_bytes")) is not int
            or item["size_bytes"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
        ):
            raise AcceptanceFailed(f"Task {task.get('task_id')} manifest contains an invalid artifact path or hash.")
    if (
        manifest["file_count"] != 1
        or manifest["returned_file_count"] != 1
        or len(manifest["files"]) != 1
        or manifest["files"][0]["path"] != "test_clamp.py"
    ):
        raise AcceptanceFailed(
            f"Task {task.get('task_id')} did not produce exactly the requested root-level test_clamp.py artifact."
        )
    final_hashes = {item["path"]: item["sha256"] for item in manifest["files"]}
    if any(final_hashes.get(path) != digest for path, digest in changed_hashes.items()):
        raise AcceptanceFailed(f"Task {task.get('task_id')} workspace delta hash does not match the verification manifest.")
    changed_paths = set(changed_hashes)

    evidence_refs = evidence.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not any(
        isinstance(ref, str) and ref.startswith("workspace:sha256:") for ref in evidence_refs
    ):
        raise AcceptanceFailed(f"Task {task.get('task_id')} receipt has no content-hash evidence reference.")

    return {
        "task_id": task.get("task_id"),
        "employee_id": task.get("assigned_employee_id"),
        "verifier_id": verifier_id,
        "attempt_id": passed_attempts[-1].get("attempt_id"),
        "test_executables": [run["executable"] for run in test_runs],
        "artifact_paths": sorted(changed_paths),
        "artifact_hashes": [item["sha256"] for item in manifest["files"]],
        "evidence_refs": evidence_refs,
    }


class Neuro13AcceptanceRunner:
    """Owner objective -> approved desktop -> real worker -> independent verifier acceptance."""

    def __init__(self) -> None:
        self.api_url = _required_env("ANCOS_NEURO13_API_URL").rstrip("/")
        parsed = urlparse(self.api_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise AcceptanceBlocked("The live mission runner only accepts a loopback API URL.")
        if _required_env("ANCOS_NEURO13_DISPOSABLE_CONFIRM") != "YES_I_WILL_DISCARD_THIS_INSTANCE":
            raise AcceptanceBlocked(
                "Set ANCOS_NEURO13_DISPOSABLE_CONFIRM=YES_I_WILL_DISCARD_THIS_INSTANCE only for a disposable stack and database."
            )
        owner_token = _required_env("ANCOS_NEURO13_OWNER_TOKEN")
        approver_token = _required_env("ANCOS_NEURO13_APPROVER_TOKEN")
        if len(owner_token) < 32 or len(approver_token) < 32 or owner_token == approver_token:
            raise AcceptanceBlocked("Distinct owner and independent approver tokens of at least 32 characters are required.")
        self.owner = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=httpx.Timeout(15.0, connect=3.0),
        )
        self.approver = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {approver_token}"},
            timeout=httpx.Timeout(15.0, connect=3.0),
        )
        self.timeout_seconds = _bounded_env_int("ANCOS_NEURO13_TIMEOUT_SECONDS", 1800, 60, 7200)
        self.poll_seconds = _bounded_env_int("ANCOS_NEURO13_POLL_SECONDS", 5, 1, 30)
        self.run_id = uuid.uuid4().hex[:12]
        self.project_id: str | None = None
        self.employee_id: str | None = None
        self.workstation_id: str | None = None
        self.report = AcceptanceReport(
            status="RUNNING",
            run_id=self.run_id,
            api_url=self.api_url,
            started_at_unix=time.time(),
        )

    async def _get(self, path: str) -> Any:
        try:
            response = await self.owner.get(path)
        except httpx.HTTPError as exc:
            raise AcceptanceBlocked(f"Core API is unreachable at {self.api_url}: {type(exc).__name__}.") from exc
        if response.status_code in {401, 403, 503}:
            raise AcceptanceBlocked(f"Core API prerequisite unavailable at GET {path}: HTTP {response.status_code}.")
        if response.is_error:
            raise AcceptanceFailed(f"Core API GET {path} failed with HTTP {response.status_code}.")
        return response.json()

    async def _post(
        self,
        client: httpx.AsyncClient,
        path: str,
        payload: dict[str, Any],
        *,
        blocked_statuses: set[int] | frozenset[int] = frozenset(),
        timeout: httpx.Timeout | float | None = None,
    ) -> Any:
        try:
            response = await client.post(path, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            if isinstance(exc, httpx.ConnectError):
                raise AcceptanceBlocked(f"Core API connection failed during POST {path}.") from exc
            raise AcceptanceFailed(f"Core API request failed during POST {path}: {type(exc).__name__}.") from exc
        if response.status_code in blocked_statuses:
            raise AcceptanceBlocked(f"Live prerequisite blocked POST {path}: HTTP {response.status_code}.")
        if response.is_error:
            raise AcceptanceFailed(f"Core API POST {path} failed with HTTP {response.status_code}.")
        return response.json()

    async def _preflight(self) -> dict[str, Any]:
        health = await self._get("/health")
        if health.get("status") != "healthy" or health.get("environment") != "production":
            raise AcceptanceBlocked("A healthy production-mode disposable ANCOS API is required.")
        boss = await self._get("/boss/status")
        if boss.get("llm_provider_configured") is not True or boss.get("ceo_status") != "ready":
            raise AcceptanceBlocked("Boss provider-backed planning is not configured; rules-only plans cannot pass NEURO-13.")
        if boss.get("worker_auth_configured") is not True or boss.get("verifier_auth_configured") is not True:
            raise AcceptanceBlocked("Distinct worker and verifier runtime credentials must be configured.")
        if boss.get("worker_runtime") != "healthy" or boss.get("verifier_runtime") != "healthy":
            raise AcceptanceBlocked("Long-running worker and independent verifier heartbeats are not healthy.")

        projects, employees, tasks = await asyncio.gather(
            self._get("/projects"), self._get("/employees"), self._get("/tasks"),
        )
        if projects or employees or tasks:
            raise AcceptanceBlocked(
                "Refusing to create mission data in a nonempty database; provide a disposable stack with empty projects, employees, and tasks."
            )
        self.report.checks.extend([
            {"name": "production_api_and_owner_auth", "passed": True},
            {"name": "provider_backed_boss_planner_available", "passed": True},
            {"name": "long_running_worker_and_verifier_healthy", "passed": True},
            {"name": "disposable_database_empty", "passed": True},
        ])
        return boss

    async def _create_mission(self) -> list[dict[str, Any]]:
        objective = (
            "Create one terminal task only in the newly recruited employee's isolated workspace; do not create "
            "phases or split this work into implementation, test-writing, or test-running tasks. Set the task's "
            "acceptance criteria exactly to `tests pass`. In that single task, create only one root-level file "
            "`test_clamp.py`. Define `clamp(value, lower, upper)` in that file to return value constrained to "
            "the inclusive bounds and raise ValueError when lower is greater than upper. In the same file, add "
            "standard-library unittest cases for below-range, above-range, in-range, both endpoints, and invalid "
            "bounds. Run `python3 -m unittest -v` from the workspace root after the file is complete. The worker "
            "and independent verifier must both pass that command. Do not claim completion before both pass."
        )
        project = await self._post(
            self.owner,
            "/boss/objective",
            {"objective": objective, "project_name": f"NEURO-13 disposable acceptance {self.run_id}"},
            blocked_statuses={401, 403, 503},
            # Planning can take longer than quick control-plane actions; the
            # client must not abandon a write that persists after timeout.
            timeout=httpx.Timeout(180.0, connect=3.0),
        )
        self.project_id = str(project.get("id", ""))
        self.report.project_id = self.project_id or None
        self.report.project_name = project.get("name")
        if not self.project_id:
            raise AcceptanceFailed("Boss objective route returned no persisted project ID.")

        plan = project.get("execution_plan") if isinstance(project.get("execution_plan"), dict) else {}
        if plan.get("plan_source") != "llm" or not plan.get("planning_model"):
            raise AcceptanceFailed("Boss objective was not planned by a persisted provider-backed model receipt.")
        _real_model_name(plan.get("planning_model"), context="Boss planner")
        phases = plan.get("phases")
        phase_list: list[Any] = phases if isinstance(phases, list) else []
        planned_tasks: list[dict[str, Any]] = []
        for phase in phase_list:
            if not isinstance(phase, dict):
                continue
            phase_tasks = phase.get("tasks")
            if isinstance(phase_tasks, list):
                planned_tasks.extend(task for task in phase_tasks if isinstance(task, dict))
        validate_terminal_acceptance_plan(planned_tasks)
        self.report.checks.append({"name": "provider_planned_persisted_terminal_dag", "passed": True, "task_count": len(planned_tasks)})

        tasks = await self._get(f"/projects/{self.project_id}/tasks")
        employees = await self._get("/employees")
        if not tasks or not employees:
            raise AcceptanceFailed("Boss did not persist work tasks and a dynamically recruited employee.")
        if not any(str(employee.get("employee_id", "")).startswith("ANC-EMP-") for employee in employees):
            raise AcceptanceFailed("Boss staffing did not create a dynamic ANC-EMP identity.")
        first_assigned = next((task for task in tasks if task.get("assigned_to")), None)
        if not first_assigned:
            raise AcceptanceFailed("Boss created no initial assigned task.")
        employee = next((item for item in employees if item.get("id") == first_assigned["assigned_to"]), None)
        if not employee:
            raise AcceptanceFailed("Initial task assignee is absent from the live employee roster.")
        self.employee_id = str(employee["employee_id"])
        self.report.employee_id = self.employee_id
        self.report.checks.append({"name": "dynamic_employee_recruited_and_task_assigned", "passed": True})
        return tasks

    async def _approve(self, action_type: str, *, docker_id: str | None = None) -> str:
        payload: dict[str, Any] = {
            "action_type": action_type,
            "employee_id": self.employee_id,
            "justification": f"NEURO-13 disposable acceptance run {self.run_id}; one isolated workstation lifecycle.",
        }
        if docker_id:
            payload["docker_id"] = docker_id
        approval = await self._post(self.owner, "/security/approvals", payload, blocked_statuses={401, 403, 503})
        approval_id = str(approval.get("id", ""))
        if not approval_id:
            raise AcceptanceFailed(f"Approval API returned no ID for {action_type}.")
        decided = await self._post(
            self.approver,
            f"/security/approvals/{approval_id}/decide",
            {"decision": "approved", "notes": f"Independent approval for disposable run {self.run_id}."},
            blocked_statuses={401, 403, 503},
        )
        if decided.get("status") != "approved":
            raise AcceptanceFailed(f"Independent approver did not approve {action_type}.")
        self.report.checks.append({"name": f"independent_approval_{action_type.lower()}", "passed": True})
        return approval_id

    async def _provision(self) -> None:
        assert self.employee_id
        approval_id = await self._approve("PROVISION_WORKSTATION")
        workstation = await self._post(
            self.owner,
            f"/employees/{self.employee_id}/workstation/provision?approval_id={approval_id}",
            {},
            blocked_statuses={401, 403, 503},
        )
        if workstation.get("status") != "running" or not workstation.get("docker_id"):
            raise AcceptanceFailed("Broker did not return a running, managed workstation receipt.")
        if workstation.get("employee_id") != self.employee_id:
            raise AcceptanceFailed("Provisioned workstation identity does not match the recruited employee.")
        self.workstation_id = str(workstation["docker_id"])
        self.report.workstation_id = self.workstation_id
        self.report.checks.append({"name": "approved_restricted_broker_workstation_running", "passed": True})

    async def _wait_for_verified_mission(self) -> None:
        assert self.project_id
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            try:
                project_report = await self._get(f"/boss/projects/{self.project_id}/report")
            except AcceptanceFailed as exc:
                if "HTTP 404" in str(exc):
                    raise AcceptanceBlocked("Running API does not expose the owner project-report route required for receipt validation.") from exc
                raise
            tasks = project_report.get("tasks") if isinstance(project_report.get("tasks"), list) else []
            if not tasks:
                await asyncio.sleep(self.poll_seconds)
                continue
            failed = [
                task.get("task_id", "<unknown>") for task in tasks
                if str(task.get("status", "")).lower() in {"failed", "cancelled"}
            ]
            if failed:
                raise AcceptanceFailed("Mission task(s) reached terminal unsuccessful state: " + ", ".join(failed))
            if all(str(task.get("status", "")).lower() == "completed" for task in tasks):
                validated = [validate_verified_task(task) for task in tasks]
                self.report.task_results = validated
                self.report.checks.append({"name": "all_project_tasks_independently_verified", "passed": True, "task_count": len(tasks)})
                return
            boss = await self._get("/boss/status")
            if boss.get("worker_runtime") != "healthy" or boss.get("verifier_runtime") != "healthy":
                raise AcceptanceBlocked("Worker or independent verifier heartbeat became stale while mission work was pending.")
            await asyncio.sleep(self.poll_seconds)
        raise AcceptanceFailed(
            f"Timed out after {self.timeout_seconds}s while waiting for real worker execution and independent verification."
        )

    async def _cleanup_workstation(self) -> None:
        if not self.employee_id or not self.workstation_id:
            self.report.cleanup = {"status": "not_needed", "reason": "no workstation was provisioned by this run"}
            return
        try:
            approval_id = await self._approve("TERMINATE_WORKSTATION", docker_id=self.workstation_id)
            result = await self._post(
                self.owner,
                f"/employees/{self.employee_id}/workstation/terminate?approval_id={approval_id}",
                {},
                blocked_statuses={401, 403, 503},
            )
            if result.get("status") != "terminated" or result.get("docker_id") != self.workstation_id:
                raise AcceptanceFailed("Approved workstation termination did not return a matching receipt.")
            self.report.cleanup = {
                "status": "terminated",
                "docker_id": self.workstation_id,
                "workspace_volume": "preserved_by_platform_policy",
            }
            self.report.checks.append({"name": "approved_workstation_terminated_and_lease_released", "passed": True})
        except Exception as exc:
            self.report.cleanup = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
            if self.report.status == "PASS":
                self.report.status = "FAIL"
                self.report.failures.append("Disposable workstation cleanup failed; see cleanup receipt.")

    async def run(self) -> AcceptanceReport:
        try:
            await self._preflight()
            await self._create_mission()
            await self._provision()
            await self._wait_for_verified_mission()
            # Review the actual hash-chained audit trail after the owner report.
            audit = await self._get("/observability/audit-logs/verify")
            if audit.get("valid") is not True:
                raise AcceptanceFailed("Audit hash chain did not verify after the mission.")
            self.report.checks.append({"name": "audit_hash_chain_valid", "passed": True, "record_count": audit.get("total_records")})
            self.report.status = "PASS"
        except AcceptanceBlocked as exc:
            self.report.status = "BLOCKED"
            self.report.blocked_reasons.append(str(exc))
        except (AcceptanceFailed, httpx.HTTPError, ValueError, KeyError) as exc:
            self.report.status = "FAIL"
            self.report.failures.append(f"{type(exc).__name__}: {exc}")
        finally:
            await self._cleanup_workstation()
            self.report.finished_at_unix = time.time()
            await self.owner.aclose()
            await self.approver.aclose()
        return self.report


async def async_main() -> int:
    try:
        runner = Neuro13AcceptanceRunner()
    except AcceptanceBlocked as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, indent=2))
        return 2
    report = await runner.run()
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0 if report.status == "PASS" else 2 if report.status == "BLOCKED" else 1


def main() -> None:
    try:
        code = asyncio.run(async_main())
    except KeyboardInterrupt:
        print(json.dumps({"status": "BLOCKED", "reason": "Interrupted by operator; inspect disposable runtime before retry."}, indent=2))
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
