from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from neural.decision_intelligence.calibration.reliability import calculate_ece
from neural.decision_intelligence.evaluation.latency_metrics import LatencyTracker
from neural.decision_intelligence.governance.shadow_mode import (
    ShadowModeManager,
    get_shadow_mode_manager,
)
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.governance.promotion")


class PromotionCriteria(BaseModel):
    min_episodes: int = 100
    min_accuracy: float = 0.96
    max_ece: float = 0.08
    max_p95_latency_ms: float = 40.0


class PromotionAssessment(BaseModel):
    category: str
    eligible_for_promotion: bool
    episode_count: int
    accuracy: float = Field(ge=0.0, le=1.0)
    ece: float = Field(ge=0.0, le=1.0)
    p95_latency_ms: float
    rejection_reasons: list[str] = Field(default_factory=list)


class PromotionManager:
    """
    Automated criteria checker for production rollout out of Shadow Mode (Section 11 & 13).
    Requires >= 96.0% accuracy, ECE <= 0.08, P95 < 40ms across >= 100 consecutive episodes.
    """

    def __init__(
        self,
        criteria: Optional[PromotionCriteria] = None,
        shadow_manager: Optional[ShadowModeManager] = None,
    ) -> None:
        self.criteria = criteria or PromotionCriteria()
        self.shadow_manager = shadow_manager or get_shadow_mode_manager()

    def evaluate_promotion(
        self,
        category: str,
        predictions: list[str | int],
        ground_truths: list[str | int],
        confidences: list[float],
        latencies_ms: list[float],
        auto_promote: bool = False,
    ) -> PromotionAssessment:
        n = len(predictions)
        rejection_reasons: list[str] = []

        if n < self.criteria.min_episodes:
            rejection_reasons.append(
                f"Insufficient episodes ({n} < required {self.criteria.min_episodes})"
            )

        # Accuracy
        def _match(p: str | int, y: str | int) -> bool:
            return str(p).strip().lower() == str(y).strip().lower()

        correct = sum(1 for p, y in zip(predictions, ground_truths) if _match(p, y))
        accuracy = (correct / n) if n > 0 else 0.0
        if accuracy < self.criteria.min_accuracy:
            rejection_reasons.append(
                f"Accuracy ({accuracy * 100:.1f}%) below minimum required ({self.criteria.min_accuracy * 100:.1f}%)"
            )

        # ECE calculation
        binary_truths = [1 if _match(p, y) else 0 for p, y in zip(predictions, ground_truths)]
        ece = calculate_ece(confidences, binary_truths)
        if ece > self.criteria.max_ece:
            rejection_reasons.append(
                f"Calibration error ECE ({ece:.4f}) exceeds threshold ({self.criteria.max_ece:.4f})"
            )

        # Latency P95
        tracker = LatencyTracker(target_p95_ms=self.criteria.max_p95_latency_ms)
        for lat in latencies_ms:
            tracker.record(lat)
        lat_summary = tracker.get_summary()
        p95 = lat_summary["p95_ms"]
        if p95 > self.criteria.max_p95_latency_ms:
            rejection_reasons.append(
                f"P95 latency ({p95:.1f}ms) exceeds SLA ({self.criteria.max_p95_latency_ms:.1f}ms)"
            )

        eligible = len(rejection_reasons) == 0

        if eligible and auto_promote:
            self.shadow_manager.set_shadow_mode(category, enabled=False)
            logger.info("Category successfully promoted out of shadow mode to live System 1", category=category)

        return PromotionAssessment(
            category=category,
            eligible_for_promotion=eligible,
            episode_count=n,
            accuracy=round(accuracy, 4),
            ece=round(ece, 4),
            p95_latency_ms=round(p95, 2),
            rejection_reasons=rejection_reasons,
        )
