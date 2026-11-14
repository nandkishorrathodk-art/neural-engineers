"""Execution reliability measurement for sustained ANCOS workloads.

Answers "is ANCOS reliable across many real tasks?" from persisted task and
attempt records, instead of from a single successful run. The aggregation logic
is pure so it can be unit tested without a database;
``collect_reliability_scorecard`` is the thin async collector used against a live
deployment.

Honesty rules enforced by construction:

* A success rate is computed over *decided* tasks only (completed/failed/cancelled)
  so pending work never inflates it.
* Human-relative speed is reported as ``NOT_MEASURED`` unless a real human median
  baseline was supplied. A task that never recorded a baseline is never scored.
* The dominant failure mode is surfaced verbatim, not smoothed over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from neural.constants import ExecutionMode, TaskStatus

# Task statuses that represent a final decision. Pending/assigned/running/retrying
# tasks are still in flight and must not count toward a success rate.
DECIDED_STATUSES: frozenset[str] = frozenset(
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    }
)

HUMAN_SPEED_NOT_MEASURED = "NOT_MEASURED"

# GUI-bearing modes need a live graphical desktop at verification time; terminal
# modes do not. Reported separately because their failure classes differ.
GUI_BEARING_MODES: frozenset[str] = frozenset(
    {ExecutionMode.GUI.value, ExecutionMode.GUI_ONLY.value, ExecutionMode.HYBRID.value}
)


def _mode_value(mode: Any) -> str:
    return getattr(mode, "value", mode) if mode is not None else "unknown"


def _status_value(status: Any) -> str:
    return getattr(status, "value", status) if status is not None else "unknown"


@dataclass
class ModeReliability:
    """Reliability of one execution mode over the decided tasks observed."""

    mode: str
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    in_flight: int = 0

    @property
    def decided(self) -> int:
        return self.completed + self.failed + self.cancelled

    @property
    def success_rate(self) -> float:
        """Completed / decided. ``0.0`` when nothing has been decided yet."""
        if self.decided == 0:
            return 0.0
        return round(self.completed / self.decided, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "in_flight": self.in_flight,
            "decided": self.decided,
            "success_rate": self.success_rate,
        }


@dataclass
class ReliabilityScorecard:
    """Aggregate reliability picture with an explicit speed-measurement flag."""

    per_mode: dict[str, ModeReliability] = field(default_factory=dict)
    attempt_status_counts: dict[str, int] = field(default_factory=dict)
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    human_speed_status: str = HUMAN_SPEED_NOT_MEASURED
    human_speed_ratio: float | None = None

    @property
    def total_decided(self) -> int:
        return sum(m.decided for m in self.per_mode.values())

    @property
    def total_completed(self) -> int:
        return sum(m.completed for m in self.per_mode.values())

    @property
    def overall_success_rate(self) -> float:
        if self.total_decided == 0:
            return 0.0
        return round(self.total_completed / self.total_decided, 4)

    def gui_bearing_success_rate(self) -> float | None:
        """Combined success rate for GUI-bearing modes, or ``None`` if none seen."""
        completed = sum(
            m.completed for name, m in self.per_mode.items() if name in GUI_BEARING_MODES
        )
        decided = sum(
            m.decided for name, m in self.per_mode.items() if name in GUI_BEARING_MODES
        )
        if decided == 0:
            return None
        return round(completed / decided, 4)

    def dominant_failure_mode(self) -> str | None:
        """The single most common rejection reason, or ``None`` when there is none."""
        if not self.rejection_reasons:
            return None
        return max(self.rejection_reasons.items(), key=lambda kv: kv[1])[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_success_rate": self.overall_success_rate,
            "total_completed": self.total_completed,
            "total_decided": self.total_decided,
            "per_mode": {name: m.to_dict() for name, m in sorted(self.per_mode.items())},
            "gui_bearing_success_rate": self.gui_bearing_success_rate(),
            "attempt_status_counts": dict(sorted(self.attempt_status_counts.items())),
            "dominant_failure_mode": self.dominant_failure_mode(),
            "rejection_reasons": dict(
                sorted(self.rejection_reasons.items(), key=lambda kv: -kv[1])
            ),
            "human_speed": {
                "status": self.human_speed_status,
                "ratio": self.human_speed_ratio,
            },
        }

    def to_markdown(self) -> str:
        lines = ["| Mode | Completed | Failed | In flight | Decided | Success rate |"]
        lines.append("|---|---|---|---|---|---|")
        for name, m in sorted(self.per_mode.items()):
            lines.append(
                f"| {name} | {m.completed} | {m.failed} | {m.in_flight} | "
                f"{m.decided} | {m.success_rate:.0%} |"
            )
        lines.append("")
        lines.append(
            f"Overall success rate: **{self.overall_success_rate:.0%}** "
            f"({self.total_completed}/{self.total_decided} decided tasks)"
        )
        gui_rate = self.gui_bearing_success_rate()
        if gui_rate is not None:
            lines.append(f"GUI-bearing (gui/hybrid) success rate: **{gui_rate:.0%}**")
        dominant = self.dominant_failure_mode()
        if dominant:
            lines.append(f"Dominant failure mode: {dominant}")
        lines.append(
            f"Human-relative speed: **{self.human_speed_status}**"
            + (
                f" ({self.human_speed_ratio:.2f}x)"
                if self.human_speed_ratio is not None
                else ""
            )
        )
        return "\n".join(lines)


def build_scorecard(
    task_rows: Iterable[Mapping[str, Any]],
    attempt_rows: Iterable[Mapping[str, Any]] = (),
    *,
    human_speed_ratio: float | None = None,
) -> ReliabilityScorecard:
    """Build a scorecard from task and attempt rows.

    ``task_rows`` items need ``execution_mode`` and ``status``. ``attempt_rows``
    items need ``status`` and, for rejected/failed attempts, ``error_message``.
    """
    per_mode: dict[str, ModeReliability] = {}
    for row in task_rows:
        mode = _mode_value(row.get("execution_mode"))
        status = _status_value(row.get("status"))
        bucket = per_mode.setdefault(mode, ModeReliability(mode=mode))
        if status == TaskStatus.COMPLETED.value:
            bucket.completed += 1
        elif status == TaskStatus.FAILED.value:
            bucket.failed += 1
        elif status == TaskStatus.CANCELLED.value:
            bucket.cancelled += 1
        else:
            bucket.in_flight += 1

    attempt_counts: dict[str, int] = {}
    rejection_reasons: dict[str, int] = {}
    for row in attempt_rows:
        status = str(row.get("status") or "unknown")
        attempt_counts[status] = attempt_counts.get(status, 0) + 1
        if status in {"rejected", "failed"}:
            reason = _classify_failure(row.get("error_message"))
            if reason:
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    speed_status = HUMAN_SPEED_NOT_MEASURED if human_speed_ratio is None else "MEASURED"
    return ReliabilityScorecard(
        per_mode=per_mode,
        attempt_status_counts=attempt_counts,
        rejection_reasons=rejection_reasons,
        human_speed_status=speed_status,
        human_speed_ratio=human_speed_ratio,
    )


# Ordered most-specific first: the first marker found names the failure class.
_FAILURE_MARKERS: tuple[tuple[str, str], ...] = (
    ("on-screen deliverable terms", "verifier: required on-screen terms not observed"),
    ("live desktop could not be observed", "verifier: live desktop not observed"),
    ("did not match the worker", "verifier: frame mismatch"),
    ("worker's gui loop did not report success", "verifier: worker GUI loop not successful"),
    (
        "hybrid terminal phase was not independently replayable",
        "verifier: hybrid phase not replayable",
    ),
    ("Unprocessable Entity", "worker: HTTP 422 from core-api"),
    ("Internal Server Error", "worker: HTTP 500 from core-api"),
    ("Bad Gateway", "worker: HTTP 502 from core-api"),
    ("terminal command", "terminal: non-zero command exit"),
    ("lease expired", "lease: worker lease expired"),
)


def _classify_failure(error_message: Any) -> str | None:
    if not error_message:
        return None
    text = str(error_message)
    for marker, label in _FAILURE_MARKERS:
        if marker in text:
            return label
    return "other"


async def collect_reliability_scorecard(
    session: Any,
    *,
    human_speed_ratio: float | None = None,
) -> ReliabilityScorecard:
    """Query a live database for the scorecard inputs and build the scorecard.

    Kept separate from ``build_scorecard`` so the aggregation logic stays pure and
    unit-testable; this only does the reads.
    """
    from sqlalchemy import select

    from neural.db.models import Task, TaskAttempt

    tasks = (await session.execute(select(Task))).scalars().all()
    attempts = (await session.execute(select(TaskAttempt))).scalars().all()
    task_rows: Sequence[Mapping[str, Any]] = [
        {"execution_mode": t.execution_mode, "status": t.status} for t in tasks
    ]
    attempt_rows: Sequence[Mapping[str, Any]] = [
        {"status": a.status, "error_message": a.error_message} for a in attempts
    ]
    return build_scorecard(task_rows, attempt_rows, human_speed_ratio=human_speed_ratio)
