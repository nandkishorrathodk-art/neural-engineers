from __future__ import annotations

from typing import Any

from neural.constants import EmployeeStatus
from neural.db.models import Employee
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.moe.workload_selector")


class WorkloadSelector:
    """
    Evaluates employee real-time availability, task backlog,
    and desktop GUI session requirements to prevent resource starvation.
    """

    @staticmethod
    def adjust_score_for_workload(
        candidate_fit: dict[str, Any],
        employee: Employee,
        active_task_count: int = 0,
        requires_gui: bool = False,
        desktop_slot_available: bool = True,
    ) -> dict[str, Any]:
        base_score = candidate_fit["composite_score"]
        adjusted_score = base_score

        # Availability penalty/boost
        status = getattr(employee, "status", EmployeeStatus.IDLE)
        if status == EmployeeStatus.IDLE:
            adjusted_score += 15.0  # Boost idle employees ready to start
        elif status == EmployeeStatus.WORKING:
            adjusted_score -= (active_task_count * 10.0)  # Penalize high backlog
        elif status == EmployeeStatus.SUSPENDED:
            adjusted_score -= 50.0

        # GUI requirement check
        if requires_gui and not desktop_slot_available:
            # If GUI is strictly required but no desktop slot is free, heavily deprioritize
            adjusted_score = max(0.1, adjusted_score * 0.2)

        return {
            **candidate_fit,
            "status": status.value if hasattr(status, "value") else str(status),
            "active_tasks": active_task_count,
            "requires_gui": requires_gui,
            "adjusted_score": round(max(0.1, adjusted_score), 2),
        }

