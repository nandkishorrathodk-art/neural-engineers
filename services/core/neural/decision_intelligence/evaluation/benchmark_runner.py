from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from neural.decision_intelligence.calibration.reliability import (
    calculate_brier_score,
    calculate_ece,
)
from neural.decision_intelligence.evaluation.accuracy_metrics import AccuracyEvaluator
from neural.decision_intelligence.evaluation.latency_metrics import LatencyTracker
from neural.decision_intelligence.routing.decision_router import (
    IntelligentDecisionRouter,
    get_decision_router,
)
from neural.decision_intelligence.schemas.choice import ChoiceCandidate, ChoiceQuestion
from neural.decision_intelligence.schemas.noul import NoulQuestion
from neural.decision_intelligence.schemas.score import ScoreQuestion
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.evaluation.benchmark_runner")


class BenchmarkReport(BaseModel):
    benchmark_name: str = "ANCOS-DecisionBench"
    total_scenarios: int
    passed_scenarios: int
    accuracy: float = Field(ge=0.0, le=1.0)
    macro_f1: float = Field(ge=0.0, le=1.0)
    ece: float = Field(ge=0.0, le=1.0)
    brier_score: float
    p95_latency_ms: float
    meets_latency_sla: bool
    meets_calibration_gate: bool  # ECE <= 0.08
    detailed_metrics: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRunner:
    """
    ANCOS-DecisionBench Runner (Section 8 & 13).
    Evaluates System 1 decision engine across standardized scenarios.
    """

    def __init__(self, router: Optional[IntelligentDecisionRouter] = None) -> None:
        self.router = router or get_decision_router()

    def _get_standard_scenarios(self) -> list[dict[str, Any]]:
        return [
            # CHOICE 1: Department routing
            {
                "type": "CHOICE",
                "question": ChoiceQuestion(
                    context="Write a Rust kernel extension for syscall filtering",
                    candidates=[
                        ChoiceCandidate(id="eng", label="Engineering Department"),
                        ChoiceCandidate(id="sec", label="Security Department"),
                        ChoiceCandidate(id="qa", label="QA Department"),
                        ChoiceCandidate(id="fin", label="Finance Department"),
                    ],
                    domain="routing",
                ),
                "expected": "eng",
            },
            # CHOICE 2: Security routing
            {
                "type": "CHOICE",
                "question": ChoiceQuestion(
                    context="Audit private keys and cryptographic certificates for leaks",
                    candidates=[
                        ChoiceCandidate(id="eng", label="Engineering Department"),
                        ChoiceCandidate(id="sec", label="Security Department"),
                        ChoiceCandidate(id="qa", label="QA Department"),
                    ],
                    domain="routing",
                ),
                "expected": "sec",
            },
            # CHOICE 3: GUI candidate pick
            {
                "type": "CHOICE",
                "question": ChoiceQuestion(
                    context="Save current financial summary as CSV spreadsheet file",
                    candidates=[
                        ChoiceCandidate(id="btn_pdf", label="Export as PDF"),
                        ChoiceCandidate(id="btn_csv", label="Export as CSV"),
                        ChoiceCandidate(id="btn_cancel", label="Cancel Operation"),
                    ],
                    domain="gui",
                ),
                "expected": "btn_csv",
            },
            # SCORE 1: Critical urgency
            {
                "type": "SCORE",
                "question": ScoreQuestion(
                    proposition="Database primary lock deadlock in production cluster",
                    context="Immediate critical blocker",
                    scale_min=1,
                    scale_max=5,
                    domain="urgency",
                ),
                "expected": 5,
            },
            # SCORE 2: Low urgency
            {
                "type": "SCORE",
                "question": ScoreQuestion(
                    proposition="Update copyright year notice in footer documentation",
                    context="Routine minor chore",
                    scale_min=1,
                    scale_max=5,
                    domain="urgency",
                ),
                "expected": 1,
            },
            # NOUL 1: Deadlock proposition
            {
                "type": "NOUL",
                "question": NoulQuestion(
                    proposition="Worker process crashed with memory allocation failure error",
                    context="fatal failure detected",
                    threshold=0.5,
                    domain="blocker",
                ),
                "expected": True,
            },
            # NOUL 2: Normal operation
            {
                "type": "NOUL",
                "question": NoulQuestion(
                    proposition="All unit tests passed with healthy execution receipts",
                    context="normal healthy stable",
                    threshold=0.5,
                    domain="blocker",
                ),
                "expected": False,
            },
        ]

    async def run_benchmark(self, custom_scenarios: Optional[list[dict[str, Any]]] = None) -> BenchmarkReport:
        scenarios = custom_scenarios or self._get_standard_scenarios()
        y_true: list[str | int] = []
        y_pred: list[str | int] = []
        confidences: list[float] = []
        probabilities_for_calib: list[float] = []
        binary_truths_for_calib: list[int] = []

        latency_tracker = LatencyTracker(target_p95_ms=40.0)

        for sc in scenarios:
            stype = sc["type"]
            expected = sc["expected"]

            if stype == "CHOICE":
                res = await self.router.route_choice(sc["question"])
                y_true.append(str(expected))
                y_pred.append(str(res.selected_id))
                confidences.append(res.confidence)
                latency_tracker.record(res.latency_ms)

                # Track calibration signal
                is_correct = int(res.selected_id == expected)
                probabilities_for_calib.append(res.confidence)
                binary_truths_for_calib.append(is_correct)

            elif stype == "SCORE":
                res = await self.router.route_score(sc["question"])
                y_true.append(expected)
                y_pred.append(res.score)
                confidences.append(res.confidence)
                latency_tracker.record(res.latency_ms)

                is_correct = int(res.score == expected)
                probabilities_for_calib.append(res.confidence)
                binary_truths_for_calib.append(is_correct)

            elif stype == "NOUL":
                res = await self.router.route_noul(sc["question"])
                y_true.append(int(expected))
                y_pred.append(int(res.is_true))
                confidences.append(res.confidence)
                latency_tracker.record(res.latency_ms)

                is_correct = int(res.is_true == expected)
                probabilities_for_calib.append(res.calibrated_prob)
                binary_truths_for_calib.append(int(expected))

        # Metrics calculation
        class_metrics = AccuracyEvaluator.compute_classification_metrics(y_true, y_pred)
        ece = calculate_ece(probabilities_for_calib, binary_truths_for_calib)
        brier = calculate_brier_score(probabilities_for_calib, binary_truths_for_calib)
        lat_summary = latency_tracker.get_summary()

        total = len(scenarios)
        passed = sum(1 for yt, yp in zip(y_true, y_pred) if yt == yp)

        return BenchmarkReport(
            total_scenarios=total,
            passed_scenarios=passed,
            accuracy=class_metrics["accuracy"],
            macro_f1=class_metrics["macro_f1"],
            ece=ece,
            brier_score=brier,
            p95_latency_ms=lat_summary["p95_ms"],
            meets_latency_sla=lat_summary["meets_sla"],
            meets_calibration_gate=ece <= 0.08,
            detailed_metrics={
                "latency": lat_summary,
                "classification": class_metrics,
            },
        )
