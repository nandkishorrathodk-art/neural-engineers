from __future__ import annotations

import time
from typing import Any, Callable

from neural.cognitive_fabric.intelligence_loops.cycle_guard import (
    ControlledLoopGuard,
    CycleDetectedError,
    LoopBudgetExceededError,
)
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.loops.gui")


class ControlledGUIExecutionLoop:
    """
    Controlled GUI Execution Loop:
    Observe → Reason → Act → Observe → Verify.
    Adheres strictly to the Phase 7 Mandate:
    - Pure screenshot-only visual grounding (no DOM or OCR bypasses).
    - Dynamic target coordinates derived from fresh screenshots.
    - Verified action receipts.
    - Protected by ControlledLoopGuard against visual deadlocks, infinite clicking, and budget overruns.
    """

    def __init__(
        self,
        max_steps: int = 10,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    async def execute(
        self,
        task_id: str,
        employee_id: str,
        goal: str,
        observe_screenshot_fn: Callable[[], dict[str, Any]],
        reason_action_fn: Callable[[dict[str, Any], str], dict[str, Any]],
        execute_action_fn: Callable[[dict[str, Any]], dict[str, Any]],
        verify_outcome_fn: Callable[[dict[str, Any], str], tuple[bool, str]],
    ) -> dict[str, Any]:
        guard = ControlledLoopGuard(
            loop_name=f"GUILoop-{task_id}",
            max_iterations=self.max_steps,
            timeout_seconds=self.timeout_seconds,
        )

        status = "in_progress"
        action_receipts: list[dict[str, Any]] = []
        final_observation: dict[str, Any] = {}
        verification_message = ""

        try:
            while status == "in_progress":
                # 1. Observe: Fresh screenshot capture
                obs = observe_screenshot_fn()
                final_observation = obs
                frame_hash = obs.get("frame_hash", f"frame_{guard.iteration}")

                # 2. Reason: Decide next action based solely on fresh screenshot
                planned_action = reason_action_fn(obs, goal)
                action_type = planned_action.get("action_type", "done")

                # Record loop state with CycleGuard to detect repeating screenshot states / clicking
                state_sig = {
                    "frame_hash": frame_hash,
                    "action_type": action_type,
                    "coords": planned_action.get("coordinates"),
                }
                guard.record_step(
                    step_name="observe_reason_act",
                    state=state_sig,
                    notes=f"Action: {action_type}",
                )

                if action_type.lower() in ("done", "finish", "complete"):
                    # 4. Verify postcondition from fresh observation
                    is_complete, msg = verify_outcome_fn(obs, goal)
                    verification_message = msg
                    status = "verified_success" if is_complete else "verification_failed"
                    break

                # 3. Act: Dispatch verified action to workstation desktop
                receipt = execute_action_fn(planned_action)
                receipt["step"] = guard.iteration
                receipt["timestamp"] = time.time()
                action_receipts.append(receipt)

                # Small settle delay for desktop UI render
                time.sleep(0.01)

        except (CycleDetectedError, LoopBudgetExceededError) as e:
            logger.warning("GUI loop terminated early by guard", error=str(e))
            status = "escalated"
            verification_message = str(e)

        return {
            "task_id": task_id,
            "employee_id": employee_id,
            "status": status,
            "is_success": status == "verified_success",
            "steps_executed": guard.iteration,
            "action_receipts": action_receipts,
            "verification_message": verification_message,
            "final_observation": final_observation,
            "audit_report": guard.get_audit_report(),
        }
