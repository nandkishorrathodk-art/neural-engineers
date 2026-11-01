from __future__ import annotations

from typing import Any, Callable, Optional

from neural.cognitive_fabric.intelligence_loops.cycle_guard import (
    ControlledLoopGuard,
    CycleDetectedError,
    LoopBudgetExceededError,
)
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.loops.recovery")


class ControlledRecoveryLoop:
    """
    Recovery Loop: Failure -> Diagnose -> Recover -> Retry or Escalate.
    Safely handles task and execution errors without endless retry storms.
    """

    def __init__(
        self,
        max_retries: int = 3,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

    async def execute_recovery(
        self,
        task_id: str,
        initial_error: str,
        diagnosis_fn: Optional[Callable[[str], str]] = None,
        recovery_action_fn: Optional[Callable[[str], bool]] = None,
    ) -> dict[str, Any]:
        guard = ControlledLoopGuard(
            loop_name=f"RecoveryLoop-{task_id}",
            max_iterations=self.max_retries,
            timeout_seconds=self.timeout_seconds,
        )

        current_error = initial_error
        status = "recovering"
        action_history = []

        try:
            while status == "recovering":
                # Step 1: Diagnose
                root_cause = diagnosis_fn(current_error) if diagnosis_fn else f"Identified cause: {current_error}"

                # Step 2: Recover
                recovered = False
                if recovery_action_fn:
                    recovered = recovery_action_fn(root_cause)

                action_history.append({"error": current_error, "root_cause": root_cause, "recovered": recovered})

                guard.record_step(
                    step_name="diagnose_and_recover",
                    state={"cause": root_cause, "attempt": guard.iteration},
                )

                if recovered:
                    status = "recovered"
                    break

                # If not recovered, prepare next attempt or escalate
                if guard.iteration >= self.max_retries:
                    status = "escalated"
                    break

        except (CycleDetectedError, LoopBudgetExceededError) as e:
            logger.warning("Recovery loop halted by guard", error=str(e))
            status = "escalated"

        return {
            "task_id": task_id,
            "status": status,
            "attempts": guard.iteration,
            "action_history": action_history,
            "escalated_to_boss": status == "escalated",
            "audit_report": guard.get_audit_report(),
        }

