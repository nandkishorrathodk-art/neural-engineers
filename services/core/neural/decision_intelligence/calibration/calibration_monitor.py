from __future__ import annotations

from collections import deque
from typing import Any, Optional

from neural.decision_intelligence.calibration.reliability import (
    calculate_brier_score,
    calculate_ece,
)
from neural.exceptions import CalibrationDriftError
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.calibration.calibration_monitor")


class CalibrationMonitor:
    """
    Runtime Calibration Drift Detector (Section 8.1 & 13).
    Tracks rolling window of System 1 predictions and ground-truth outcomes.
    Detects when ECE exceeds allowable threshold (<= 0.08).
    """

    def __init__(
        self,
        max_ece_threshold: float = 0.08,
        window_size: int = 200,
        min_samples_for_eval: int = 20,
    ) -> None:
        self.max_ece_threshold = max_ece_threshold
        self.window_size = window_size
        self.min_samples_for_eval = min_samples_for_eval

        # Storage per category: deque of (probability, ground_truth)
        self._history: dict[str, deque[tuple[float, int]]] = {}

    def record(self, probability: float, outcome: int, category: str = "general") -> None:
        if category not in self._history:
            self._history[category] = deque(maxlen=self.window_size)
        self._history[category].append((probability, outcome))

    def get_metrics(self, category: Optional[str] = None) -> dict[str, Any]:
        probs: list[float] = []
        truths: list[int] = []

        if category and category in self._history:
            items = list(self._history[category])
        else:
            items = []
            for q in self._history.values():
                items.extend(list(q))

        for p, y in items:
            probs.append(p)
            truths.append(y)

        sample_count = len(probs)
        if sample_count < self.min_samples_for_eval:
            return {
                "sample_count": sample_count,
                "status": "insufficient_samples",
                "ece": 0.0,
                "brier_score": 0.0,
                "is_calibrated": True,
            }

        ece = calculate_ece(probs, truths)
        brier = calculate_brier_score(probs, truths)
        is_calibrated = ece <= self.max_ece_threshold

        return {
            "sample_count": sample_count,
            "status": "evaluated",
            "ece": ece,
            "brier_score": brier,
            "max_ece_threshold": self.max_ece_threshold,
            "is_calibrated": is_calibrated,
        }

    def check_drift(self, category: Optional[str] = None, raise_on_drift: bool = False) -> tuple[bool, float]:
        metrics = self.get_metrics(category)
        if metrics["status"] == "insufficient_samples":
            return False, 0.0

        ece = metrics["ece"]
        has_drifted = ece > self.max_ece_threshold
        if has_drifted:
            logger.warning("Calibration drift detected", ece=ece, threshold=self.max_ece_threshold, category=category)
            if raise_on_drift:
                raise CalibrationDriftError(
                    f"Calibration drift detected: ECE={ece:.4f} > allowable {self.max_ece_threshold}"
                )
        return has_drifted, ece

    def reset(self, category: Optional[str] = None) -> None:
        if category:
            if category in self._history:
                self._history[category].clear()
        else:
            self._history.clear()


_monitor_instance: Optional[CalibrationMonitor] = None


def get_calibration_monitor() -> CalibrationMonitor:
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = CalibrationMonitor()
    return _monitor_instance


def reset_calibration_monitor() -> None:
    global _monitor_instance
    _monitor_instance = None

