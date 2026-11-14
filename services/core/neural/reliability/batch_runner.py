"""NEURO-12A post-fix reliability batch runner.

Answers "is GUI/hybrid execution reliable after the ``54a256c`` verification
fix, across many real objectives?" by driving real objectives through the live
control plane and measuring outcomes, instead of inferring reliability from one
successful run or from a batch-runner unit test.

Two layers:

* **Pure measurement logic** (this module's dataclasses and ``build_*`` helpers)
  turns raw per-objective observations into per-mode statistics. It is
  deliberately free of I/O so it can be unit tested without a deployment.
* **Live driver** (``Neuro12BatchRunner``) submits objectives, provisions the
  employee desktop (the objective route does not do this automatically), waits
  for a terminal state, samples resource usage, and terminates the desktop.

Honesty rules enforced by construction:

* A statistical sample is one *unique objective*, never an individual retry.
  Retries are reported separately as retry frequency.
* ``pending``/``running``/in-flight objectives and timed-out objectives are kept
  in their own buckets and never counted as successes.
* Success rate is computed over *decided* objectives only.
* Every execution receipt and verification failure reason is preserved verbatim.
* Pre/post-fix comparison refuses to run across non-comparable workloads.

Deployment constraint this module accounts for: ``ANCOS_MAX_ACTIVE_DESKTOPS``
is ``1`` on the current instance, so GUI/hybrid objectives run strictly serially.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from neural.constants import TaskStatus

REQUIRED_FIX_COMMIT = "54a256c"

# Final outcome buckets. ``timeout`` is synthetic (a task that never reached a
# terminal state within the cohort budget) and is deliberately distinct from a
# genuine task failure.
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_IN_FLIGHT = "in_flight"

_DECIDED_OUTCOMES = frozenset(
    {OUTCOME_COMPLETED, OUTCOME_FAILED, OUTCOME_CANCELLED, OUTCOME_TIMEOUT}
)

GUI_MODE = "gui"
HYBRID_MODE = "hybrid"

COHORT_SMOKE = "smoke"
COHORT_EXTENDED = "extended"

SMOKE_SAMPLES_PER_MODE = 10
EXTENDED_SAMPLES_PER_MODE = 30
MAX_ACCEPTANCE_SAMPLES_PER_MODE = 100


def classify_outcome(status: Any, *, timed_out: bool = False) -> str:
    """Map a live task status to a final outcome bucket.

    ``timed_out`` forces the synthetic ``timeout`` bucket regardless of the
    (non-terminal) status the task was left in.
    """
    if timed_out:
        return OUTCOME_TIMEOUT
    value = getattr(status, "value", status)
    value = str(value) if value is not None else ""
    if value == TaskStatus.COMPLETED.value:
        return OUTCOME_COMPLETED
    if value == TaskStatus.FAILED.value:
        return OUTCOME_FAILED
    if value == TaskStatus.CANCELLED.value:
        return OUTCOME_CANCELLED
    return OUTCOME_IN_FLIGHT


@dataclass(frozen=True)
class EnvironmentFingerprint:
    """Exact environment a cohort was measured against (requirement 2/3)."""

    head_commit: str
    head_subject: str
    branch: str
    required_fix_commit: str
    required_fix_commit_present: bool
    worker_config: dict[str, Any] = field(default_factory=dict)
    desktop_image: str = ""
    model_config: dict[str, Any] = field(default_factory=dict)
    captured_at_unix: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def blocking_reasons(self) -> list[str]:
        """Reasons the environment is unfit to measure the post-fix cohort."""
        reasons: list[str] = []
        if not self.required_fix_commit_present:
            reasons.append(
                f"Tested code does not contain the required fix commit {self.required_fix_commit}."
            )
        if not self.head_commit:
            reasons.append("HEAD commit could not be resolved.")
        return reasons


def build_environment_fingerprint(
    *,
    head_commit: str,
    head_subject: str,
    branch: str,
    required_fix_commit_present: bool,
    worker_config: Mapping[str, Any],
    desktop_image: str,
    model_config: Mapping[str, Any],
    required_fix_commit: str = REQUIRED_FIX_COMMIT,
    captured_at_unix: float | None = None,
) -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        head_commit=head_commit,
        head_subject=head_subject,
        branch=branch,
        required_fix_commit=required_fix_commit,
        required_fix_commit_present=bool(required_fix_commit_present),
        worker_config=dict(worker_config),
        desktop_image=desktop_image,
        model_config=dict(model_config),
        captured_at_unix=captured_at_unix if captured_at_unix is not None else time.time(),
    )


@dataclass
class ObjectiveObservation:
    """One real objective attempt, the unit of statistical sampling."""

    sample_id: str
    mode: str
    cohort: str
    objective: str
    project_id: str = ""
    task_id: str = ""
    final_status: str = ""
    outcome: str = OUTCOME_IN_FLIGHT
    verified: bool = False
    attempts: int = 0
    retries: int = 0
    planner_resubmissions: int = 0
    latency_seconds: float | None = None
    cost_usd: float = 0.0
    contract_violation: str = ""
    receipts: list[dict[str, Any]] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    resource_samples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_decided(self) -> bool:
        return self.outcome in _DECIDED_OUTCOMES

    @property
    def counted_as_sample(self) -> bool:
        """A sample counts statistically only when it reached a decision.

        Contract violations (the planner produced the wrong mode or shape) are
        not execution outcomes and are excluded from success rates.
        """
        return self.is_decided and not self.contract_violation

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["is_decided"] = self.is_decided
        data["counted_as_sample"] = self.counted_as_sample
        return data


def extract_receipt_cost_usd(receipts: Iterable[Mapping[str, Any]]) -> float:
    """Sum every real model cost recorded in a task's attempt receipts.

    Cost is reported, never asserted: a receipt that carries no model cost
    contributes zero rather than being fabricated, and a reported total of
    ``0.0`` means "no cost was recorded", not "execution was free". Both a GUI
    receipt's top-level ``model`` and a hybrid receipt's per-phase ``model``
    blocks are read, so hybrid cost is not silently dropped. The control-plane
    report exposes the same figure under ``execution_model``.
    """
    total = 0.0
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            continue
        execution = receipt.get("execution_receipt")
        if isinstance(execution, Mapping):
            total += _sum_model_cost(execution.get("model"))
            phases = execution.get("phases")
            if isinstance(phases, list):
                for phase in phases:
                    if isinstance(phase, Mapping):
                        total += _sum_model_cost(phase.get("model"))
        total += _sum_model_cost(receipt.get("model"))
        total += _sum_model_cost(receipt.get("execution_model"))
    return round(total, 8)


def _sum_model_cost(model: Any) -> float:
    if not isinstance(model, Mapping):
        return 0.0
    cost = model.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return 0.0
    if cost < 0 or cost > 1_000_000:
        return 0.0
    return float(cost)


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


@dataclass
class ModeCohortStats:
    """Per-mode reliability statistics for one cohort."""

    mode: str
    cohort: str
    samples_submitted: int = 0
    counted_samples: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    timed_out: int = 0
    in_flight: int = 0
    contract_violations: int = 0
    total_retries: int = 0
    total_planner_resubmissions: int = 0
    planner_retried_samples: int = 0
    latencies: list[float] = field(default_factory=list)
    total_cost_usd: float = 0.0
    peak_cpu_percent: float | None = None
    peak_mem_mib: float | None = None
    failure_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def decided(self) -> int:
        return self.completed + self.failed + self.cancelled + self.timed_out

    @property
    def success_rate(self) -> float:
        if self.decided == 0:
            return 0.0
        return round(self.completed / self.decided, 4)

    @property
    def timeout_rate(self) -> float:
        if self.decided == 0:
            return 0.0
        return round(self.timed_out / self.decided, 4)

    @property
    def retry_frequency(self) -> float:
        """Mean retries per counted sample (unique objective)."""
        if self.counted_samples == 0:
            return 0.0
        return round(self.total_retries / self.counted_samples, 4)

    @property
    def mean_latency_seconds(self) -> float | None:
        if not self.latencies:
            return None
        return round(sum(self.latencies) / len(self.latencies), 2)

    @property
    def p95_latency_seconds(self) -> float | None:
        value = _percentile(self.latencies, 0.95)
        return None if value is None else round(value, 2)

    @property
    def planner_first_try_rate(self) -> float | None:
        """Fraction of counted samples the planner got right on the first submission."""
        if self.counted_samples == 0:
            return None
        first_try = self.counted_samples - self.planner_retried_samples
        return round(first_try / self.counted_samples, 4)

    @property
    def cost_per_sample_usd(self) -> float:
        if self.counted_samples == 0:
            return 0.0
        return round(self.total_cost_usd / self.counted_samples, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "cohort": self.cohort,
            "samples_submitted": self.samples_submitted,
            "counted_samples": self.counted_samples,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "timed_out": self.timed_out,
            "in_flight": self.in_flight,
            "contract_violations": self.contract_violations,
            "decided": self.decided,
            "success_rate": self.success_rate,
            "timeout_rate": self.timeout_rate,
            "retry_frequency": self.retry_frequency,
            "total_retries": self.total_retries,
            "planner_retried_samples": self.planner_retried_samples,
            "total_planner_resubmissions": self.total_planner_resubmissions,
            "planner_first_try_rate": self.planner_first_try_rate,
            "mean_latency_seconds": self.mean_latency_seconds,
            "p95_latency_seconds": self.p95_latency_seconds,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "cost_per_sample_usd": self.cost_per_sample_usd,
            "peak_cpu_percent": self.peak_cpu_percent,
            "peak_mem_mib": self.peak_mem_mib,
            "failure_reasons": dict(sorted(self.failure_reasons.items(), key=lambda kv: -kv[1])),
        }


def build_mode_cohort_stats(
    observations: Iterable[ObjectiveObservation], *, mode: str, cohort: str
) -> ModeCohortStats:
    """Aggregate observations for one (mode, cohort) pair.

    Only *decided, non-contract-violation* observations contribute to the
    success/timeout denominators; in-flight and violated objectives stay visible
    in their own buckets.
    """
    stats = ModeCohortStats(mode=mode, cohort=cohort)
    for obs in observations:
        if obs.mode != mode or obs.cohort != cohort:
            continue
        stats.samples_submitted += 1
        stats.total_cost_usd += obs.cost_usd
        for sample in obs.resource_samples:
            cpu = sample.get("cpu_percent")
            mem = sample.get("mem_mib")
            if isinstance(cpu, (int, float)):
                stats.peak_cpu_percent = max(stats.peak_cpu_percent or 0.0, float(cpu))
            if isinstance(mem, (int, float)):
                stats.peak_mem_mib = max(stats.peak_mem_mib or 0.0, float(mem))
        if obs.contract_violation:
            stats.contract_violations += 1
            continue
        if not obs.is_decided:
            stats.in_flight += 1
            continue
        stats.counted_samples += 1
        stats.total_retries += obs.retries
        if obs.planner_resubmissions > 1:
            stats.planner_retried_samples += 1
            stats.total_planner_resubmissions += obs.planner_resubmissions - 1
        if obs.outcome == OUTCOME_COMPLETED:
            stats.completed += 1
        elif obs.outcome == OUTCOME_FAILED:
            stats.failed += 1
        elif obs.outcome == OUTCOME_CANCELLED:
            stats.cancelled += 1
        elif obs.outcome == OUTCOME_TIMEOUT:
            stats.timed_out += 1
        if obs.latency_seconds is not None:
            stats.latencies.append(obs.latency_seconds)
        for reason in obs.failure_reasons:
            stats.failure_reasons[reason] = stats.failure_reasons.get(reason, 0) + 1
    return stats


@dataclass
class CohortComparison:
    """Pre/post-fix comparison, valid only across comparable workloads."""

    comparable: bool
    reason: str
    pre: dict[str, Any] = field(default_factory=dict)
    post: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compare_cohorts(
    pre: ModeCohortStats,
    post: ModeCohortStats,
    *,
    pre_workload: str,
    post_workload: str,
    min_samples: int = 10,
) -> CohortComparison:
    """Compare pre-fix and post-fix stats only when the workloads are comparable.

    Comparability requires the same mode, the same workload descriptor, and at
    least ``min_samples`` decided samples on both sides. Anything less is
    reported as NON_COMPARABLE rather than as an improvement.
    """
    if pre.mode != post.mode:
        return CohortComparison(False, f"Modes differ ({pre.mode} vs {post.mode}).")
    if pre_workload != post_workload:
        return CohortComparison(
            False,
            f"Workloads differ ({pre_workload!r} vs {post_workload!r}); "
            "rates across different workloads are not comparable.",
        )
    if pre.decided < min_samples or post.decided < min_samples:
        return CohortComparison(
            False,
            f"Insufficient decided samples (pre={pre.decided}, post={post.decided}, "
            f"min={min_samples}).",
        )
    return CohortComparison(
        True,
        "Same mode, same workload, sufficient samples.",
        pre=pre.to_dict(),
        post=post.to_dict(),
    )


def evaluate_smoke_gate(
    observations: Iterable[ObjectiveObservation],
    *,
    required_per_mode: int = SMOKE_SAMPLES_PER_MODE,
    min_success_rate: float = 0.8,
) -> dict[str, Any]:
    """Decide whether the smoke cohort clears the gate to run the extended one.

    The gate is intentionally strict: every requested sample must be present and
    decided, and each mode must reach the minimum success rate. This prevents
    escalating to a 30+30 (or 100+100) cohort on partial or failing evidence.
    """
    per_mode: dict[str, Any] = {}
    passed = True
    for mode in (GUI_MODE, HYBRID_MODE):
        stats = build_mode_cohort_stats(observations, mode=mode, cohort=COHORT_SMOKE)
        enough = stats.counted_samples >= required_per_mode
        rate_ok = stats.success_rate >= min_success_rate
        mode_ok = enough and rate_ok
        passed = passed and mode_ok
        per_mode[mode] = {
            "counted_samples": stats.counted_samples,
            "required": required_per_mode,
            "success_rate": stats.success_rate,
            "min_success_rate": min_success_rate,
            "passed": mode_ok,
        }
    return {"passed": passed, "min_success_rate": min_success_rate, "per_mode": per_mode}


def render_report_markdown(
    *,
    fingerprint: EnvironmentFingerprint,
    cohort_results: Mapping[str, ModeCohortStats],
    comparisons: Sequence[CohortComparison],
    observations: Sequence[ObjectiveObservation],
    evidence_path: str,
    generated_at_utc: str,
) -> str:
    """Render the reproducible NEURO-12A report body (requirement 15)."""
    lines: list[str] = []
    lines.append("# NEURO-12A Post-Fix Reliability Report")
    lines.append("")
    lines.append(f"Generated (UTC): {generated_at_utc}")
    lines.append("")
    lines.append("## Environment fingerprint")
    lines.append("")
    lines.append(f"- Branch: `{fingerprint.branch}`")
    lines.append(f"- HEAD commit: `{fingerprint.head_commit}` — {fingerprint.head_subject}")
    lines.append(
        f"- Required fix commit `{fingerprint.required_fix_commit}` present in tested code: "
        f"**{fingerprint.required_fix_commit_present}**"
    )
    lines.append(f"- Desktop image: `{fingerprint.desktop_image}`")
    lines.append(f"- Worker config: `{json.dumps(fingerprint.worker_config, sort_keys=True)}`")
    lines.append(f"- Model config: `{json.dumps(fingerprint.model_config, sort_keys=True)}`")
    lines.append("")
    lines.append("## Results by mode and cohort")
    lines.append("")
    lines.append(
        "| Cohort | Mode | Samples | Counted | Completed | Failed | Cancelled | "
        "Timed out | In flight | Contract violations | Success rate | Timeout rate | "
        "Retry freq | Mean latency (s) | p95 latency (s) | Cost (USD) | Cost/sample | "
        "Peak CPU % | Peak mem MiB |"
    )
    lines.append("|" + "---|" * 19)
    for key in sorted(cohort_results):
        stats = cohort_results[key]
        lines.append(
            f"| {stats.cohort} | {stats.mode} | {stats.samples_submitted} | "
            f"{stats.counted_samples} | {stats.completed} | {stats.failed} | "
            f"{stats.cancelled} | {stats.timed_out} | {stats.in_flight} | "
            f"{stats.contract_violations} | {stats.success_rate:.0%} | "
            f"{stats.timeout_rate:.0%} | {stats.retry_frequency} | "
            f"{_fmt(stats.mean_latency_seconds)} | {_fmt(stats.p95_latency_seconds)} | "
            f"{stats.total_cost_usd:.4f} | {stats.cost_per_sample_usd:.6f} | "
            f"{_fmt(stats.peak_cpu_percent)} | {_fmt(stats.peak_mem_mib)} |"
        )
    lines.append("")
    lines.append(
        "Statistical samples are unique objectives. Retries are reported separately "
        "as retry frequency and never inflate the sample count. In-flight, pending and "
        "timed-out objectives are kept in their own columns and never counted as "
        "successes."
    )
    lines.append("")

    lines.append("## Failure reasons (verbatim, grouped)")
    lines.append("")
    any_reason = False
    for key in sorted(cohort_results):
        stats = cohort_results[key]
        if not stats.failure_reasons:
            continue
        any_reason = True
        lines.append(f"### {stats.cohort} / {stats.mode}")
        lines.append("")
        for reason, count in sorted(stats.failure_reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"- ({count}x) {reason}")
        lines.append("")
    if not any_reason:
        lines.append("No failure reasons recorded for the measured cohorts.")
        lines.append("")

    lines.append("## Pre-fix vs post-fix comparison")
    lines.append("")
    if not comparisons:
        lines.append("No comparison requested.")
        lines.append("")
    for comparison in comparisons:
        lines.append(f"### {comparison.reason}")
        lines.append("")
        if comparison.comparable:
            pre_rate = comparison.pre.get("success_rate")
            post_rate = comparison.post.get("success_rate")
            lines.append(
                f"- Pre-fix success rate: {pre_rate:.0%} (n={comparison.pre.get('decided')})"
            )
            lines.append(
                f"- Post-fix success rate: {post_rate:.0%} (n={comparison.post.get('decided')})"
            )
        else:
            lines.append("NON_COMPARABLE — no rate delta is reported.")
        lines.append("")

    lines.append("## Preserved evidence")
    lines.append("")
    lines.append(
        f"Every objective's execution receipts, verification failure reasons and "
        f"resource samples are preserved in `{evidence_path}`."
    )
    lines.append("")
    lines.append(f"Objectives recorded: {len(observations)}")
    lines.append("")
    return "\n".join(lines)


def _fmt(value: Any) -> str:
    return "n/a" if value is None else str(value)


def sample_desktop_resources(docker_id: str) -> dict[str, Any]:
    """Sample a desktop container's CPU/memory via ``docker stats``.

    Returns an empty dict when the container or docker is unavailable, so a
    missing sample is reported as absent rather than fabricated.
    """
    if not docker_id:
        return {}
    try:
        result = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.CPUPerc}}|{{.MemUsage}}",
                docker_id,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0 or "|" not in result.stdout:
        return {}
    cpu_text, mem_text = result.stdout.strip().split("|", 1)
    sample: dict[str, Any] = {}
    try:
        sample["cpu_percent"] = float(cpu_text.strip().rstrip("%"))
    except ValueError:
        pass
    mem_value = mem_text.strip().split("/")[0].strip()
    mib = _parse_memory_to_mib(mem_value)
    if mib is not None:
        sample["mem_mib"] = mib
    return sample


_MEMORY_UNITS: tuple[tuple[str, float], ...] = (
    ("GiB", 1024.0),
    ("MiB", 1.0),
    ("KiB", 1.0 / 1024.0),
    ("B", 1.0 / (1024.0 * 1024.0)),
)


def _parse_memory_to_mib(text: str) -> float | None:
    text = text.strip()
    for suffix, factor in _MEMORY_UNITS:
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            try:
                return float(number) * factor
            except ValueError:
                return None
    return None


def gui_objective(sample_id: str) -> str:
    """A single-task, GUI-only objective with a detectable on-screen marker.

    The marker is short and fully uppercase with numeric components
    (``NEURO12A-GUI-001``) because both the worker's postcondition check and the
    independent verifier read the on-screen text through OCR and an
    uppercase-token regex; a long, lowercase-bearing marker is read only in part
    and fails closed even when the GUI work succeeded.

    The leading constraint block is required because the Boss planner is a live
    language model and will otherwise sometimes decompose a small objective into
    several tasks. A decomposed objective is recorded as a contract violation,
    not as an execution failure, so the constraint keeps the cohort measuring
    execution reliability rather than planner variance.
    """
    marker = _gui_marker(sample_id)
    return (
        "CRITICAL CONSTRAINTS: Return exactly ONE phase with exactly ONE task. That "
        'task\'s execution_mode MUST be exactly the string "gui" (not terminal, not '
        "terminal_only, not hybrid). Do NOT return multiple tasks. Using ONLY the "
        "graphical desktop, open the default text editor and type the exact string "
        f"{marker} into a new document. Do not create, modify, or run any files from "
        "the terminal."
    )


def _gui_marker(sample_id: str) -> str:
    """Derive a short, uppercase, OCR-friendly marker from a sample id.

    ``<run>-gui-003`` becomes ``NEURO12A-GUI-003``. The run token is dropped so
    the on-screen string stays short; per-run identity is carried in the report,
    not on screen.
    """
    index = sample_id.rsplit("-", 1)[-1]
    return f"NEURO12A-GUI-{index}"


def hybrid_objective(sample_id: str) -> str:
    """A single-task hybrid objective faithful to the proven Lifecycle 4 shape.

    The stage prose is deliberately identical to the objective that passed
    independent verification after ``54a256c``; only a leading constraint block is
    added so the live planner does not decompose it. Changing the stage prose
    (e.g. a per-sample filename) alters how the terminal planner excludes the
    GUI-owned line and produces false negatives, so the contract text is held
    constant and reliability is measured against the proven contract.
    """
    return (
        "CRITICAL CONSTRAINTS: Return exactly ONE phase with exactly ONE task. That "
        'task\'s execution_mode MUST be exactly the string "hybrid" (not terminal, not '
        "gui, not terminal_only). Do NOT return multiple tasks. A single hybrid task is "
        "the only acceptable plan because the same task needs both a terminal and a "
        "graphical desktop. Stage 1 (terminal): create handoff.txt in the workspace "
        "containing exactly the two lines ANCOS_HYBRID_TEST and TERMINAL_PREPARED=1. "
        "Stage 2 (graphical desktop only): open handoff.txt in the text editor via the "
        "graphical desktop and add the line GUI_EDIT=HYBRID_GUI_OK, then save the "
        "document using the GUI. Stage 3 (terminal): read handoff.txt and print its "
        "SHA-256 hash."
    )


def objective_for_mode(mode: str, sample_id: str) -> str:
    if mode == GUI_MODE:
        return gui_objective(sample_id)
    if mode == HYBRID_MODE:
        return hybrid_objective(sample_id)
    raise ValueError(f"Unsupported cohort mode: {mode!r}")


def extract_failure_reasons(task: Mapping[str, Any]) -> list[str]:
    """Collect verbatim failure/verification reasons from a task report row."""
    reasons: list[str] = []
    notes = task.get("verification_notes")
    if isinstance(notes, str) and notes.strip():
        reasons.append(notes.strip())
    for attempt in task.get("attempts") or []:
        if not isinstance(attempt, Mapping):
            continue
        status = str(attempt.get("status") or "")
        message = attempt.get("error_message")
        if isinstance(message, str) and message.strip():
            reasons.append(message.strip())
        elif status in {"rejected", "failed"}:
            reasons.append(f"attempt status: {status}")
    return reasons


def summarize_attempts(task: Mapping[str, Any]) -> tuple[int, int]:
    """Return (attempt_count, retry_count) from a task report row."""
    attempts = task.get("attempts") or []
    count = len(attempts) if isinstance(attempts, list) else 0
    retry_count = task.get("retry_count")
    if not isinstance(retry_count, int):
        retry_count = max(0, count - 1)
    return count, retry_count
