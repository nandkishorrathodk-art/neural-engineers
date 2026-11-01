from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.benchmarks.human_speed")


@dataclass
class HumanSpeedComparisonResult:
    task_id: str
    task_name: str
    human_time_seconds: float
    ancos_time_seconds: float
    speedup_ratio: float
    meets_4x_target: bool
    passed_verification: bool
    disqualified: bool
    disqualification_reason: Optional[str]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HumanSpeedComparator:
    """
    ANCOS-HumanSpeed 4x Baseline Comparator (Section 14 & 15).
    Computes empirical speedup ratio:
      Speedup = T_human / T_ancos
    Enforces Disqualification Rule:
      If task output fails verification or violates safety policy, Speedup = 0.0.
      A failed task cannot be counted as a fast success!
    """

    SPEEDUP_TARGET: float = 4.0

    @classmethod
    def evaluate_speedup(
        cls,
        task_id: str,
        task_name: str,
        human_time_seconds: float,
        ancos_time_seconds: float,
        passed_verification: bool,
        safety_violation: bool = False,
        disqualification_reason: Optional[str] = None,
    ) -> HumanSpeedComparisonResult:
        """
        Evaluates task speedup against human baseline.
        """
        if human_time_seconds <= 0.0:
            raise ValueError("human_time_seconds must be positive")
        if ancos_time_seconds <= 0.0:
            raise ValueError("ancos_time_seconds must be positive")

        # Disqualification rule check
        disqualified = False
        reason = disqualification_reason

        if not passed_verification:
            disqualified = True
            reason = reason or "Task deliverable failed independent visual verification"
        elif safety_violation:
            disqualified = True
            reason = reason or "Task triggered a safety policy or approval gate violation"

        if disqualified:
            speedup = 0.0
            meets_target = False
        else:
            speedup = round(human_time_seconds / ancos_time_seconds, 2)
            meets_target = speedup >= cls.SPEEDUP_TARGET

        logger.info(
            "HumanSpeed evaluation complete",
            task_id=task_id,
            speedup=speedup,
            meets_target=meets_target,
            disqualified=disqualified,
        )

        return HumanSpeedComparisonResult(
            task_id=task_id,
            task_name=task_name,
            human_time_seconds=human_time_seconds,
            ancos_time_seconds=ancos_time_seconds,
            speedup_ratio=speedup,
            meets_4x_target=meets_target,
            passed_verification=passed_verification,
            disqualified=disqualified,
            disqualification_reason=reason,
            details={
                "speedup_target": cls.SPEEDUP_TARGET,
                "latency_reduction_percent": (
                    round((1.0 - (ancos_time_seconds / human_time_seconds)) * 100.0, 1)
                    if not disqualified
                    else 0.0
                ),
            },
        )

