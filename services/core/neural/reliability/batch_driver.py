"""Live driver for the NEURO-12A post-fix reliability batch.

Drives real GUI and hybrid objectives through the live ANCOS control plane and
records honest per-objective observations. Measurement logic lives in
``batch_runner``; this module only does I/O.

Operational realities this driver handles:

* The objective route assigns an employee but does **not** provision the
  graphical desktop, and GUI/hybrid tasks cannot be claimed without a running
  desktop. The driver therefore provisions and terminates the employee desktop
  around each objective, using the real owner/approver approval flow.
* ``ANCOS_MAX_ACTIVE_DESKTOPS`` is ``1`` on the current instance, so objectives
  run strictly serially. Concurrency is a deliberate non-goal here.
* The live database is non-empty, so samples are identified by run-scoped
  objective markers rather than by wiping the database.
* Code must not change during an active cohort: the driver re-checks HEAD before
  and after and marks the cohort invalid if it moved.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from neural.reliability.batch_runner import (
    COHORT_EXTENDED,
    COHORT_SMOKE,
    GUI_MODE,
    HYBRID_MODE,
    OUTCOME_IN_FLIGHT,
    OUTCOME_TIMEOUT,
    REQUIRED_FIX_COMMIT,
    CohortComparison,
    EnvironmentFingerprint,
    ObjectiveObservation,
    build_environment_fingerprint,
    build_mode_cohort_stats,
    classify_outcome,
    compare_cohorts,
    evaluate_smoke_gate,
    extract_failure_reasons,
    extract_receipt_cost_usd,
    objective_for_mode,
    render_report_markdown,
    sample_desktop_resources,
    summarize_attempts,
)

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class BatchBlocked(RuntimeError):
    """Raised when a hard prerequisite for a valid measurement is missing."""


def _git(*args: str, cwd: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def resolve_git_state(repo_dir: str | Path, required_fix_commit: str) -> dict[str, Any]:
    """Resolve HEAD, branch, and whether the required fix commit is an ancestor."""
    repo = str(repo_dir)
    head = _git("rev-parse", "HEAD", cwd=repo)
    subject = _git("log", "-1", "--format=%s", cwd=repo)
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", required_fix_commit, "HEAD"],
        capture_output=True,
        cwd=repo,
        check=False,
    )
    return {
        "head_commit": head,
        "head_subject": subject,
        "branch": branch,
        "required_fix_commit_present": ancestor.returncode == 0,
    }


@dataclass
class BatchReport:
    status: str = "RUNNING"
    run_id: str = ""
    cohort: str = COHORT_SMOKE
    started_at_utc: str = ""
    finished_at_utc: str = ""
    fingerprint: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    cohort_stats: dict[str, Any] = field(default_factory=dict)
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    smoke_gate: dict[str, Any] = field(default_factory=dict)
    blocked_reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    head_stable_during_cohort: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Neuro12BatchRunner:
    def __init__(
        self,
        *,
        api_url: str,
        owner_token: str,
        approver_token: str,
        repo_dir: str | Path,
        cohort: str = COHORT_SMOKE,
        samples_per_mode: int | None = None,
        poll_seconds: float = 5.0,
        task_timeout_seconds: float = 420.0,
        provision_timeout_seconds: float = 180.0,
    ) -> None:
        if not api_url.startswith(("http://localhost", "http://127.0.0.1")):
            raise BatchBlocked("The batch driver only accepts a loopback API URL.")
        if len(owner_token) < 32 or len(approver_token) < 32:
            raise BatchBlocked("Owner and approver tokens of at least 32 characters are required.")
        if owner_token == approver_token:
            raise BatchBlocked("Owner and approver tokens must be distinct identities.")
        self.api_url = api_url.rstrip("/")
        self.repo_dir = Path(repo_dir)
        self.cohort = cohort
        self.samples_per_mode = samples_per_mode
        self.poll_seconds = poll_seconds
        self.task_timeout_seconds = task_timeout_seconds
        self.provision_timeout_seconds = provision_timeout_seconds
        self.run_id = uuid.uuid4().hex[:10]
        self._owner = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {owner_token}"},
            timeout=httpx.Timeout(180.0, connect=5.0),
        )
        self._approver = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {approver_token}"},
            timeout=httpx.Timeout(60.0, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._owner.aclose()
        await self._approver.aclose()

    # -- low level ---------------------------------------------------------

    async def _get(self, path: str) -> Any:
        response = await self._owner.get(path)
        if response.is_error:
            raise BatchBlocked(f"GET {path} failed with HTTP {response.status_code}.")
        return response.json()

    async def _post_owner(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        return await self._owner.post(path, json=payload)

    async def _approve(
        self, action_type: str, employee_id: str, *, docker_id: str | None = None
    ) -> str:
        payload: dict[str, Any] = {
            "action_type": action_type,
            "employee_id": employee_id,
            "justification": f"NEURO-12A cohort {self.cohort} run {self.run_id}.",
        }
        if docker_id:
            payload["docker_id"] = docker_id
        request = await self._post_owner("/security/approvals", payload)
        if request.status_code != 200:
            raise BatchBlocked(
                f"Approval request for {action_type} failed: HTTP {request.status_code}."
            )
        approval_id = str(request.json().get("id", ""))
        decide = await self._approver.post(
            f"/security/approvals/{approval_id}/decide",
            json={"decision": "approved", "notes": f"Independent approval for run {self.run_id}."},
        )
        if decide.status_code != 200 or decide.json().get("status") != "approved":
            raise BatchBlocked(f"Independent approver did not approve {action_type}.")
        return approval_id

    # -- environment fingerprint ------------------------------------------

    async def capture_fingerprint(self) -> EnvironmentFingerprint:
        git_state = resolve_git_state(self.repo_dir, REQUIRED_FIX_COMMIT)
        boss = await self._get("/boss/status")
        worker_config: dict[str, Any] = {
            "worker_runtime": boss.get("worker_runtime"),
            "verifier_runtime": boss.get("verifier_runtime"),
            "worker_auth_configured": boss.get("worker_auth_configured"),
            "verifier_auth_configured": boss.get("verifier_auth_configured"),
            "service_runtimes": boss.get("service_runtimes"),
        }
        model_config: dict[str, Any] = {}
        try:
            routes = await self._get("/llm/routes")
            model_config["tiers"] = routes.get("tiers")
            model_config["routes"] = routes.get("routes")
        except BatchBlocked:
            model_config["routes"] = "unavailable"
        return build_environment_fingerprint(
            head_commit=git_state["head_commit"],
            head_subject=git_state["head_subject"],
            branch=git_state["branch"],
            required_fix_commit_present=git_state["required_fix_commit_present"],
            worker_config=worker_config,
            desktop_image=await self._desktop_image(),
            model_config=model_config,
        )

    async def _desktop_image(self) -> str:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=ancos-desktop-",
                "--format",
                "{{.Image}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        images = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return ", ".join(sorted(images)) if images else "ancos/employee-base:latest"

    # -- one objective -----------------------------------------------------

    async def _run_objective(
        self, mode: str, sample_id: str, *, max_contract_retries: int = 6
    ) -> ObjectiveObservation:
        """Submit and execute one objective, retrying planner contract violations.

        The Boss planner is a live language model that occasionally decomposes a
        small objective into several tasks or assigns the wrong execution mode.
        Commit ``54a256c`` fixed *execution and verification* defects, not
        planning, so this cohort measures execution reliability given a correct
        task contract: a sample is resubmitted until the planner yields exactly
        one task of the requested mode. The number of resubmissions is recorded so
        planner variance stays visible rather than hidden.
        """
        objective = objective_for_mode(mode, sample_id)
        observation = ObjectiveObservation(
            sample_id=sample_id,
            mode=mode,
            cohort=self.cohort,
            objective=objective,
        )
        started = time.monotonic()
        for submission in range(1, max_contract_retries + 1):
            outcome = await self._attempt_submission(mode, sample_id, observation)
            if outcome == "contract_violation":
                observation.contract_violation = ""
                observation.failure_reasons = []
                observation.project_id = ""
                observation.task_id = ""
                observation.planner_resubmissions = submission
                continue
            observation.planner_resubmissions = submission
            observation.latency_seconds = round(time.monotonic() - started, 2)
            return observation
        # Every submission violated the contract: record it as such.
        observation.contract_violation = (
            observation.contract_violation
            or f"planner violated the {mode} contract on {max_contract_retries} submissions"
        )
        observation.outcome = OUTCOME_IN_FLIGHT
        observation.planner_resubmissions = max_contract_retries
        observation.latency_seconds = round(time.monotonic() - started, 2)
        return observation

    async def _attempt_submission(
        self, mode: str, sample_id: str, observation: ObjectiveObservation
    ) -> str:
        """Run one submission attempt. Returns ``"contract_violation"`` on a bad plan."""
        employee_id = ""
        docker_id = ""
        try:
            submit = await self._post_owner(
                "/boss/objective",
                {
                    "objective": observation.objective,
                    "project_name": f"NEURO12A {mode} {sample_id}",
                },
            )
            if submit.status_code != 200:
                observation.contract_violation = (
                    f"objective submission failed: HTTP {submit.status_code} {submit.text[:200]}"
                )
                return "contract_violation"
            project = submit.json()
            observation.project_id = str(project.get("id", ""))

            task, employee_id, contract_issue = await self._locate_task(
                observation.project_id, mode
            )
            if contract_issue:
                observation.contract_violation = contract_issue
                return "contract_violation"
            if task is None or not employee_id:
                observation.contract_violation = "planner produced no assigned task/employee"
                return "contract_violation"
            observation.task_id = str(task.get("task_id", ""))

            provision_approval = await self._approve("PROVISION_WORKSTATION", employee_id)
            provision = await self._owner.post(
                f"/employees/{employee_id}/workstation/provision?approval_id={provision_approval}",
                json={},
            )
            if provision.status_code != 200:
                observation.failure_reasons.append(
                    f"provision failed: HTTP {provision.status_code} {provision.text[:200]}"
                )
                observation.outcome = classify_outcome("failed")
                return "executed"
            docker_id = str(provision.json().get("docker_id", ""))

            await self._wait_for_terminal(observation, docker_id)
            return "executed"
        except (httpx.HTTPError, BatchBlocked) as exc:
            observation.failure_reasons.append(f"{type(exc).__name__}: {exc}")
            observation.outcome = OUTCOME_IN_FLIGHT
            return "executed"
        finally:
            if docker_id and employee_id:
                await self._terminate_workstation(employee_id, docker_id)

    async def _locate_task(
        self, project_id: str, mode: str
    ) -> tuple[dict[str, Any] | None, str, str]:
        report = await self._get(f"/boss/projects/{project_id}/report")
        tasks = report.get("tasks") or []
        if len(tasks) != 1:
            return None, "", f"expected exactly one task, planner produced {len(tasks)}"
        task = tasks[0]
        task_mode = str(task.get("execution_mode", "")).lower()
        if task_mode != mode:
            return None, "", f"planner produced mode {task_mode!r}, expected {mode!r}"
        employee_id = str(task.get("assigned_employee_id") or "")
        return task, employee_id, ""

    async def _wait_for_terminal(self, observation: ObjectiveObservation, docker_id: str) -> None:
        deadline = time.monotonic() + self.task_timeout_seconds
        task: dict[str, Any] = {}
        while time.monotonic() < deadline:
            report = await self._get(f"/boss/projects/{observation.project_id}/report")
            tasks = report.get("tasks") or []
            if tasks:
                task = tasks[0]
            status = str(task.get("status", "")).lower()
            sample = sample_desktop_resources(docker_id)
            if sample:
                observation.resource_samples.append(sample)
            if status in _TERMINAL_STATUSES:
                observation.final_status = status
                observation.outcome = classify_outcome(status)
                observation.verified = bool(task.get("verified"))
                observation.attempts, observation.retries = summarize_attempts(task)
                observation.receipts = _task_receipts(task)
                observation.cost_usd = extract_receipt_cost_usd(observation.receipts)
                observation.failure_reasons = extract_failure_reasons(task)
                return
            await asyncio.sleep(self.poll_seconds)
        observation.final_status = str(task.get("status", "unknown")).lower()
        observation.outcome = OUTCOME_TIMEOUT
        observation.attempts, observation.retries = summarize_attempts(task)
        observation.receipts = _task_receipts(task)
        observation.cost_usd = extract_receipt_cost_usd(observation.receipts)
        observation.failure_reasons = extract_failure_reasons(task)
        if not observation.failure_reasons:
            observation.failure_reasons.append(
                f"objective did not reach a terminal state within {self.task_timeout_seconds:.0f}s"
            )

    async def _terminate_workstation(self, employee_id: str, docker_id: str) -> None:
        try:
            approval_id = await self._approve(
                "TERMINATE_WORKSTATION", employee_id, docker_id=docker_id
            )
            await self._owner.post(
                f"/employees/{employee_id}/workstation/terminate?approval_id={approval_id}",
                json={},
            )
        except (httpx.HTTPError, BatchBlocked):
            pass

    # -- cohort orchestration ---------------------------------------------

    async def run_cohort(self, cohort: str) -> tuple[list[ObjectiveObservation], dict[str, Any]]:
        """Run one cohort serially (desktop capacity is 1) and return observations."""
        self.cohort = cohort
        if self.samples_per_mode is not None:
            samples_per_mode = self.samples_per_mode
        else:
            samples_per_mode = 10 if cohort == COHORT_SMOKE else 30
        observations: list[ObjectiveObservation] = []
        for mode in (GUI_MODE, HYBRID_MODE):
            for index in range(1, samples_per_mode + 1):
                sample_id = f"{self.run_id}-{mode}-{index:03d}"
                observation = await self._run_objective(mode, sample_id)
                observations.append(observation)
                print(
                    json.dumps(
                        {
                            "sample": sample_id,
                            "mode": mode,
                            "outcome": observation.outcome,
                            "status": observation.final_status,
                            "verified": observation.verified,
                            "attempts": observation.attempts,
                            "latency_s": observation.latency_seconds,
                        }
                    ),
                    flush=True,
                )
        return observations, {}


def _task_receipts(task: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for attempt in task.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        receipts.append(
            {
                "attempt_id": attempt.get("attempt_id"),
                "status": attempt.get("status"),
                "worker_id": attempt.get("worker_id"),
                "verifier_id": attempt.get("verifier_id"),
                "execution_receipt": attempt.get("execution_receipt"),
                "execution_model": attempt.get("execution_model"),
                "verification_receipt": attempt.get("verification_receipt"),
                "error_message": attempt.get("error_message"),
            }
        )
    evidence = task.get("verification_evidence")
    if isinstance(evidence, dict) and evidence:
        receipts.append({"task_verification_evidence": evidence})
    return receipts


async def _main_async(args: Any) -> int:
    runner = Neuro12BatchRunner(
        api_url=args.api_url,
        owner_token=args.owner_token,
        approver_token=args.approver_token,
        repo_dir=args.repo_dir,
        cohort=args.cohort,
        samples_per_mode=args.samples_per_mode,
        poll_seconds=args.poll_seconds,
        task_timeout_seconds=args.task_timeout,
    )
    report = BatchReport(
        run_id=runner.run_id,
        cohort=args.cohort,
        started_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    try:
        fingerprint = await runner.capture_fingerprint()
        report.fingerprint = fingerprint.to_dict()
        blocking = fingerprint.blocking_reasons()
        if blocking:
            report.status = "BLOCKED"
            report.blocked_reasons = blocking
            _emit_report(report, args)
            return 2
        head_before = fingerprint.head_commit

        smoke_observations, _ = await runner.run_cohort(COHORT_SMOKE)
        gate = evaluate_smoke_gate(smoke_observations)
        report.smoke_gate = gate
        all_observations = list(smoke_observations)
        if args.cohort == COHORT_EXTENDED or args.run_extended_if_smoke_passes:
            if gate["passed"]:
                extended_observations, _ = await runner.run_cohort(COHORT_EXTENDED)
                all_observations.extend(extended_observations)
            else:
                report.notes.append(
                    "Extended cohort skipped: smoke gate not passed "
                    f"({json.dumps(gate, sort_keys=True)})."
                )
        else:
            report.notes.append("Extended cohort not requested for this run.")

        head_after = resolve_git_state(args.repo_dir, REQUIRED_FIX_COMMIT)["head_commit"]
        report.head_stable_during_cohort = head_after == head_before
        if not report.head_stable_during_cohort:
            report.notes.append(
                f"HEAD changed during the cohort ({head_before} -> {head_after}); "
                "the measurement is invalid and must be re-run."
            )

        report.observations = [obs.to_dict() for obs in all_observations]
        report.cohort_stats = _cohort_stats(all_observations)
        report.comparisons = _comparisons(all_observations)
        report.status = "PASS" if gate["passed"] else "FAIL"
        _emit_report(report, args, fingerprint=fingerprint, observations=all_observations)
        return 0 if report.status == "PASS" else 1
    except BatchBlocked as exc:
        report.status = "BLOCKED"
        report.blocked_reasons.append(str(exc))
        _emit_report(report, args)
        return 2
    finally:
        await runner.aclose()
        report.finished_at_utc = datetime.now(timezone.utc).isoformat()


def _cohort_stats(observations: list[ObjectiveObservation]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for cohort in (COHORT_SMOKE, COHORT_EXTENDED):
        for mode in (GUI_MODE, HYBRID_MODE):
            mode_stats = build_mode_cohort_stats(observations, mode=mode, cohort=cohort)
            if mode_stats.samples_submitted == 0:
                continue
            stats[f"{cohort}/{mode}"] = mode_stats.to_dict()
    return stats


def _comparisons(observations: list[ObjectiveObservation]) -> list[dict[str, Any]]:
    """Compare smoke vs extended only when they describe the same workload.

    Pre-fix figures come from the persisted baseline, not from this runner; here
    we only guard that smoke and extended describe comparable workloads so a rate
    delta between them is meaningful.
    """
    comparisons = []
    for mode in (GUI_MODE, HYBRID_MODE):
        smoke = build_mode_cohort_stats(observations, mode=mode, cohort=COHORT_SMOKE)
        extended = build_mode_cohort_stats(observations, mode=mode, cohort=COHORT_EXTENDED)
        if smoke.samples_submitted == 0 or extended.samples_submitted == 0:
            continue
        comparison = compare_cohorts(
            smoke, extended, pre_workload="smoke", post_workload="extended"
        )
        comparisons.append(comparison.to_dict())
    return comparisons


def _emit_report(
    report: BatchReport,
    args: Any,
    *,
    fingerprint: EnvironmentFingerprint | None = None,
    observations: list[ObjectiveObservation] | None = None,
) -> None:
    report.finished_at_utc = report.finished_at_utc or datetime.now(timezone.utc).isoformat()
    evidence_path = Path(args.evidence_out)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    if fingerprint is not None and observations:
        cohort_results = {
            key: build_mode_cohort_stats(
                observations, mode=key.split("/")[1], cohort=key.split("/")[0]
            )
            for key in report.cohort_stats
        }
        comparisons = [CohortComparison(**comparison) for comparison in report.comparisons]
        markdown = render_report_markdown(
            fingerprint=fingerprint,
            cohort_results=cohort_results,
            comparisons=comparisons,
            observations=observations,
            evidence_path=str(evidence_path),
            generated_at_utc=report.finished_at_utc,
        )
        Path(args.report_out).write_text(markdown)
    print(json.dumps({"status": report.status, "evidence": str(evidence_path)}, indent=2))


def main() -> None:  # pragma: no cover - CLI entry
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(description="NEURO-12A post-fix reliability batch runner")
    parser.add_argument(
        "--api-url", default=os.environ.get("ANCOS_API_URL", "http://localhost:12000")
    )
    parser.add_argument("--owner-token", default=os.environ.get("OWNER_API_TOKEN", ""))
    parser.add_argument("--approver-token", default=os.environ.get("APPROVER_API_TOKEN", ""))
    parser.add_argument("--repo-dir", default=str(Path(__file__).resolve().parents[4]))
    parser.add_argument("--cohort", choices=[COHORT_SMOKE, COHORT_EXTENDED], default=COHORT_SMOKE)
    parser.add_argument(
        "--samples-per-mode",
        type=int,
        default=None,
        help="Override samples per mode (used for a small validation run).",
    )
    parser.add_argument("--run-extended-if-smoke-passes", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--task-timeout", type=float, default=420.0)
    parser.add_argument(
        "--evidence-out", default="docs/neuro/evidence/neuro12a_batch_evidence.json"
    )
    parser.add_argument("--report-out", default="docs/neuro/NEURO12_POST_FIX_RELIABILITY_REPORT.md")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main_async(args)))


if __name__ == "__main__":  # pragma: no cover
    main()
