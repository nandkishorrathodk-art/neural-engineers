from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from neural.constants import TaskPriority
from neural.logger import get_logger

logger = get_logger("ancos.resource_scheduler.queue")

PRIORITY_RANK = {
    TaskPriority.CRITICAL: 4,
    TaskPriority.HIGH: 3,
    TaskPriority.MEDIUM: 2,
    TaskPriority.LOW: 1,
}


@dataclass
class QueuedStandbyEmployee:
    employee_id: str
    task_id: str
    department: str
    priority: TaskPriority = TaskPriority.MEDIUM
    enqueued_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def wait_time_seconds(self) -> float:
        return time.time() - self.enqueued_at


class EmployeeExecutionQueue:
    """
    Fair & Priority-Aware Execution Queue for Standby Employees.
    Prevents starvation across departments and prioritizes critical missions.
    """

    def __init__(self) -> None:
        self._queue: list[QueuedStandbyEmployee] = []
        self._last_served_department: Optional[str] = None

    def enqueue(
        self,
        employee_id: str,
        task_id: str,
        department: str,
        priority: TaskPriority = TaskPriority.MEDIUM,
        metadata: Optional[dict[str, Any]] = None,
    ) -> QueuedStandbyEmployee:
        # Check if already in queue
        for item in self._queue:
            if item.employee_id == employee_id and item.task_id == task_id:
                return item

        item = QueuedStandbyEmployee(
            employee_id=employee_id,
            task_id=task_id,
            department=department,
            priority=priority,
            metadata=metadata or {},
        )
        self._queue.append(item)
        logger.info(
            "Enqueued standby employee for desktop slot",
            employee_id=employee_id,
            task_id=task_id,
            department=department,
            priority=priority.value,
        )
        return item

    def dequeue(self) -> Optional[QueuedStandbyEmployee]:
        if not self._queue:
            return None

        # Sort key:
        # 1. Priority rank descending (CRITICAL first)
        # 2. Department fairness bonus (if different from last served department)
        # 3. Wait time descending (longest waiting first)
        def sort_key(item: QueuedStandbyEmployee) -> tuple[int, int, float]:
            p_val = PRIORITY_RANK.get(item.priority, 2)
            dept_bonus = 1 if item.department != self._last_served_department else 0
            return (p_val, dept_bonus, item.wait_time_seconds)

        self._queue.sort(key=sort_key, reverse=True)
        selected = self._queue.pop(0)
        self._last_served_department = selected.department
        logger.info(
            "Dequeued standby employee for slot allocation",
            employee_id=selected.employee_id,
            task_id=selected.task_id,
            wait_time=round(selected.wait_time_seconds, 2),
        )
        return selected

    def remove(self, employee_id: str) -> bool:
        initial_len = len(self._queue)
        self._queue = [item for item in self._queue if item.employee_id != employee_id]
        return len(self._queue) < initial_len

    def size(self) -> int:
        return len(self._queue)

    def is_empty(self) -> bool:
        return len(self._queue) == 0

    def list_queue(self) -> list[dict[str, Any]]:
        return [
            {
                "employee_id": item.employee_id,
                "task_id": item.task_id,
                "department": item.department,
                "priority": item.priority.value,
                "wait_time_seconds": round(item.wait_time_seconds, 2),
            }
            for item in self._queue
        ]

