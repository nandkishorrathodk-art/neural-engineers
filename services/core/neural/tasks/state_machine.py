from datetime import datetime, timezone
from typing import Any, Optional, Union

from neural.constants import TaskStatus
from neural.exceptions import InvalidTaskStateTransitionError
from neural.logger import get_logger

logger = get_logger("ancos.tasks.state_machine")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TaskStateMachine:
    """
    Formal Task State Machine.
    Governs lifecycle states:
    PENDING -> ASSIGNED -> RUNNING -> COMPLETED / FAILED / RETRYING / BLOCKED
    Enforces deterministic state transitions and prevents illegal jumps.
    """

    VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
        TaskStatus.PENDING: {
            TaskStatus.ASSIGNED,
            TaskStatus.RUNNING,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.ASSIGNED: {
            TaskStatus.RUNNING,
            TaskStatus.PENDING,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RUNNING: {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.RETRYING,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.RETRYING: {
            TaskStatus.RUNNING,
            TaskStatus.PENDING,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.BLOCKED: {
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
        },
        TaskStatus.FAILED: {
            TaskStatus.RETRYING,
            TaskStatus.PENDING,
            TaskStatus.CANCELLED,
        },
        TaskStatus.COMPLETED: set(),  # Terminal state
        TaskStatus.CANCELLED: set(),  # Terminal state
    }

    @classmethod
    def _coerce_status(cls, status: Union[TaskStatus, str]) -> TaskStatus:
        if isinstance(status, TaskStatus):
            return status
        try:
            return TaskStatus(str(status).lower())
        except ValueError as err:
            raise InvalidTaskStateTransitionError(f"Unknown task status: '{status}'") from err

    @classmethod
    def can_transition(
        cls,
        from_status: Union[TaskStatus, str],
        to_status: Union[TaskStatus, str]
    ) -> bool:
        """Checks if a transition between two states is valid."""
        src = cls._coerce_status(from_status)
        dest = cls._coerce_status(to_status)

        # Self-transition is allowed as idempotent no-op
        if src == dest:
            return True

        allowed = cls.VALID_TRANSITIONS.get(src, set())
        return dest in allowed

    @classmethod
    def validate_transition(
        cls,
        from_status: Union[TaskStatus, str],
        to_status: Union[TaskStatus, str]
    ) -> None:
        """
        Validates transition. Raises InvalidTaskStateTransitionError if illegal.
        """
        src = cls._coerce_status(from_status)
        dest = cls._coerce_status(to_status)

        if not cls.can_transition(src, dest):
            allowed = [s.value for s in cls.get_allowed_transitions(src)]
            raise InvalidTaskStateTransitionError(
                f"Illegal task state transition from '{src.value}' to '{dest.value}'. "
                f"Allowed target states: {allowed or 'None (Terminal state)'}."
            )

    @classmethod
    def transition(
        cls,
        task: Any,
        to_status: Union[TaskStatus, str],
        reason: Optional[str] = None
    ) -> TaskStatus:
        """
        Executes a validated state transition on a Task object.
        Updates status, lifecycle timestamps, and logs transition.
        """
        current_status = getattr(task, "status", TaskStatus.PENDING)
        src = cls._coerce_status(current_status)
        dest = cls._coerce_status(to_status)

        cls.validate_transition(src, dest)

        if src == dest:
            return dest

        task.status = dest

        if dest == TaskStatus.RUNNING:
            if not getattr(task, "started_at", None):
                task.started_at = utc_now()
        elif dest in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.completed_at = utc_now()
        elif dest == TaskStatus.RETRYING:
            current_retries = getattr(task, "retry_count", 0)
            task.retry_count = current_retries + 1

        logger.info(
            "Task state transitioned",
            task_id=getattr(task, "task_id", getattr(task, "id", "unknown")),
            from_state=src.value,
            to_state=dest.value,
            reason=reason
        )
        return dest

    @classmethod
    def get_allowed_transitions(
        cls,
        from_status: Union[TaskStatus, str]
    ) -> list[TaskStatus]:
        """Returns list of allowed destination states from current status."""
        src = cls._coerce_status(from_status)
        return sorted(list(cls.VALID_TRANSITIONS.get(src, set())), key=lambda x: x.value)

    @classmethod
    def is_terminal(cls, status: Union[TaskStatus, str]) -> bool:
        """Returns True if status is a terminal state."""
        src = cls._coerce_status(status)
        return len(cls.VALID_TRANSITIONS.get(src, set())) == 0
