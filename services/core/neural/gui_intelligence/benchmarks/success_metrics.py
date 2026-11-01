from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.benchmarks.success_metrics")


@dataclass
class TurnMetricRecord:
    turn_index: int
    action_type: str
    capture_ms: float
    encoding_ms: float
    grounding_ms: float
    motor_ms: float
    settle_ms: float
    total_ms: float
    is_chunk: bool = False
    chunk_size: int = 1


@dataclass
class SubsystemMetricsSummary:
    total_actions: int = 0
    total_chunks: int = 0
    average_turn_latency_ms: float = 0.0
    average_capture_ms: float = 0.0
    average_grounding_ms: float = 0.0
    average_motor_ms: float = 0.0
    fast_brain_invocations: int = 0
    deep_brain_invocations: int = 0
    escalation_count: int = 0
    fast_brain_ratio: float = 0.0
    compliance_with_300ms_budget: bool = True
    total_token_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GUISuccessMetrics:
    """
    Quantitative Accuracy, Latency & Token Cost Metrics Tracker (Section 15 & 16).
    Tracks granular per-action latencies across all perception and action subsystems.
    Validates compliance with the <= 300ms Fast Brain turn latency budget.
    """

    MAX_TURN_BUDGET_MS: float = 300.0

    def __init__(self) -> None:
        self.records: list[TurnMetricRecord] = []
        self.deep_brain_calls: int = 0
        self.fast_brain_calls: int = 0
        self.escalations: int = 0
        self.token_cost_usd: float = 0.0

    def record_turn(
        self,
        action_type: str,
        capture_ms: float,
        encoding_ms: float,
        grounding_ms: float,
        motor_ms: float,
        settle_ms: float,
        is_chunk: bool = False,
        chunk_size: int = 1,
    ) -> TurnMetricRecord:
        """Records granular latency components for an action turn."""
        total_ms = capture_ms + encoding_ms + grounding_ms + motor_ms + settle_ms
        turn = TurnMetricRecord(
            turn_index=len(self.records) + 1,
            action_type=action_type,
            capture_ms=capture_ms,
            encoding_ms=encoding_ms,
            grounding_ms=grounding_ms,
            motor_ms=motor_ms,
            settle_ms=settle_ms,
            total_ms=total_ms,
            is_chunk=is_chunk,
            chunk_size=chunk_size,
        )
        self.records.append(turn)
        return turn

    def record_brain_call(self, is_deep: bool) -> None:
        if is_deep:
            self.deep_brain_calls += 1
        else:
            self.fast_brain_calls += 1

    def record_escalation(self) -> None:
        self.escalations += 1

    def add_token_cost(self, cost_usd: float) -> None:
        self.token_cost_usd += cost_usd

    def summarize(self) -> SubsystemMetricsSummary:
        """Aggregates all recorded turn metrics into a SubsystemMetricsSummary."""
        n = len(self.records)
        if n == 0:
            return SubsystemMetricsSummary()

        avg_total = sum(r.total_ms for r in self.records) / n
        avg_cap = sum(r.capture_ms for r in self.records) / n
        avg_ground = sum(r.grounding_ms for r in self.records) / n
        avg_motor = sum(r.motor_ms for r in self.records) / n

        chunks = sum(1 for r in self.records if r.is_chunk)
        total_brain_calls = self.fast_brain_calls + self.deep_brain_calls
        fast_ratio = (
            self.fast_brain_calls / total_brain_calls if total_brain_calls > 0 else 0.0
        )

        return SubsystemMetricsSummary(
            total_actions=n,
            total_chunks=chunks,
            average_turn_latency_ms=round(avg_total, 2),
            average_capture_ms=round(avg_cap, 2),
            average_grounding_ms=round(avg_ground, 2),
            average_motor_ms=round(avg_motor, 2),
            fast_brain_invocations=self.fast_brain_calls,
            deep_brain_invocations=self.deep_brain_calls,
            escalation_count=self.escalations,
            fast_brain_ratio=round(fast_ratio, 3),
            compliance_with_300ms_budget=avg_total <= self.MAX_TURN_BUDGET_MS,
            total_token_cost_usd=round(self.token_cost_usd, 4),
        )
