from __future__ import annotations

from typing import Optional

from neural.decision_intelligence.system_one.contracts import DecisionPrimitive
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.calibration.thresholds")

# Calibrated domain thresholds from architecture spec (Sections 3 & 8)
DEFAULT_CALIBRATED_THRESHOLDS: dict[str, float] = {
    # Primitives defaults
    "DEFAULT_CHOICE": 0.85,
    "DEFAULT_SCORE": 0.80,
    "DEFAULT_NOUL": 0.85,

    # Domain specific
    "routing:CHOICE": 0.85,
    "gui:CHOICE": 0.88,
    "specialist:CHOICE": 0.85,
    "urgency:SCORE": 0.80,
    "severity:SCORE": 0.82,
    "complexity:SCORE": 0.80,
    "blocker:NOUL": 0.85,
    "anomaly:NOUL": 0.90,
    "safety:NOUL": 0.95,
    "human_escalation:NOUL": 0.92,
}


class ThresholdManager:
    """
    Manages calibrated confidence thresholds across domains and decision primitives.
    Enforces strict confidence gates (Section 3).
    """

    def __init__(self, custom_thresholds: Optional[dict[str, float]] = None) -> None:
        self._thresholds: dict[str, float] = dict(DEFAULT_CALIBRATED_THRESHOLDS)
        if custom_thresholds:
            self._thresholds.update(custom_thresholds)

    def get_threshold(self, domain: str, primitive: DecisionPrimitive) -> float:
        key = f"{domain.lower()}:{primitive.value}"
        if key in self._thresholds:
            return self._thresholds[key]

        default_key = f"DEFAULT_{primitive.value}"
        return self._thresholds.get(default_key, 0.85)

    def set_threshold(self, domain: str, primitive: DecisionPrimitive, threshold: float) -> None:
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"Threshold must be in [0.0, 1.0], got {threshold}")
        key = f"{domain.lower()}:{primitive.value}"
        self._thresholds[key] = threshold
        logger.info("Updated calibrated threshold", key=key, threshold=threshold)

    def list_all(self) -> dict[str, float]:
        return dict(self._thresholds)


_threshold_manager_instance: Optional[ThresholdManager] = None


def get_threshold_manager() -> ThresholdManager:
    global _threshold_manager_instance
    if _threshold_manager_instance is None:
        _threshold_manager_instance = ThresholdManager()
    return _threshold_manager_instance


def reset_threshold_manager() -> None:
    global _threshold_manager_instance
    _threshold_manager_instance = None

