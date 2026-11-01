from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from neural.constants import TaskPriority
from neural.logger import get_logger

logger = get_logger("ancos.shared_inference.queue")


@dataclass
class QueuedInferenceRequest:
    request_id: str
    employee_id: str
    task_id: str
    payload: Any
    priority: TaskPriority = TaskPriority.MEDIUM
    future: Optional[asyncio.Future] = None
    enqueued_at: float = field(default_factory=time.time)

    @property
    def priority_val(self) -> int:
        ranks = {
            TaskPriority.CRITICAL: 4,
            TaskPriority.HIGH: 3,
            TaskPriority.MEDIUM: 2,
            TaskPriority.LOW: 1,
        }
        return ranks.get(self.priority, 2)


class SharedInferenceQueue:
    """
    Fair-Share Inference Queue with Anti-Starvation Guarantees.
    Maintains per-employee request context and prevents single employees from
    monopolizing shared model inference bandwidth.
    """

    def __init__(
        self,
        max_concurrent_in_flight: int = 8,
        max_per_employee_in_flight: int = 2,
    ) -> None:
        self.max_concurrent = max_concurrent_in_flight
        self.max_per_emp = max_per_employee_in_flight
        self._queue: list[QueuedInferenceRequest] = []
        self._in_flight: dict[str, int] = {}  # employee_id -> count
        self._total_in_flight = 0

    def enqueue(
        self,
        request_id: str,
        employee_id: str,
        task_id: str,
        payload: Any,
        priority: TaskPriority = TaskPriority.MEDIUM,
    ) -> QueuedInferenceRequest:
        req = QueuedInferenceRequest(
            request_id=request_id,
            employee_id=employee_id,
            task_id=task_id,
            payload=payload,
            priority=priority,
        )
        self._queue.append(req)
        return req

    def get_next_dispatchable(self) -> Optional[QueuedInferenceRequest]:
        if not self._queue or self._total_in_flight >= self.max_concurrent:
            return None

        # Sort candidate requests:
        # 1. Employees under their quota preferred
        # 2. Priority rank descending
        # 3. FIFO order
        def sort_key(r: QueuedInferenceRequest) -> tuple[int, int, float]:
            emp_inflight = self._in_flight.get(r.employee_id, 0)
            under_quota = 1 if emp_inflight < self.max_per_emp else 0
            return (under_quota, r.priority_val, -r.enqueued_at)

        self._queue.sort(key=sort_key, reverse=True)

        # Check top request
        candidate = self._queue[0]
        emp_inflight = self._in_flight.get(candidate.employee_id, 0)
        if emp_inflight >= self.max_per_emp:
            # All available requests exceed per-employee quota, must wait
            return None

        req = self._queue.pop(0)
        self._in_flight[req.employee_id] = emp_inflight + 1
        self._total_in_flight += 1
        return req

    def complete_request(self, employee_id: str) -> None:
        if employee_id in self._in_flight:
            self._in_flight[employee_id] = max(0, self._in_flight[employee_id] - 1)
        self._total_in_flight = max(0, self._total_in_flight - 1)

    def size(self) -> int:
        return len(self._queue)

