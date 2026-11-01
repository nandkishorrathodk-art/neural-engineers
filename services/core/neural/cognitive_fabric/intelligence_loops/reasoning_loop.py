from __future__ import annotations

from typing import Any, Callable, Optional

from neural.cognitive_fabric.intelligence_loops.cycle_guard import (
    ControlledLoopGuard,
    CycleDetectedError,
    LoopBudgetExceededError,
)
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.loops.reasoning")


class ControlledReasoningLoop:
    """
    Reasoning Loop: Understand -> Plan -> Evaluate -> Correct.
    Iteratively refines task plans and architectures with cycle detection
    and budget enforcement.
    """

    def __init__(
        self,
        max_iterations: int = 5,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds

    async def execute(
        self,
        objective: str,
        initial_plan: dict[str, Any],
        evaluator_fn: Optional[Callable[[dict[str, Any]], tuple[bool, list[str]]]] = None,
    ) -> dict[str, Any]:
        guard = ControlledLoopGuard(
            loop_name="ReasoningLoop",
            max_iterations=self.max_iterations,
            timeout_seconds=self.timeout_seconds,
        )
        current_plan = dict(initial_plan)
        status = "in_progress"
        corrections_applied = []

        try:
            while status == "in_progress":
                # Step 1: Evaluate
                is_valid = True
                critiques: list[str] = []
                if evaluator_fn:
                    is_valid, critiques = evaluator_fn(current_plan)
                else:
                    # Default heuristic check: requirements and phases exist
                    if "phases" not in current_plan or not current_plan["phases"]:
                        is_valid = False
                        critiques.append("Plan missing phases")

                guard.record_step(
                    step_name="evaluate_and_correct",
                    state={"plan": current_plan, "critiques": critiques},
                    tokens_used=150,
                    cost_usd=0.002,
                )

                if is_valid:
                    status = "converged"
                    break

                # Step 2: Correct
                corrections_applied.extend(critiques)
                # Apply corrective patch to plan
                if "phases" not in current_plan or not current_plan["phases"]:
                    current_plan["phases"] = [{"phase_id": 1, "name": "Initial Execution", "tasks": []}]
                current_plan["version"] = current_plan.get("version", 1) + 1

        except (CycleDetectedError, LoopBudgetExceededError) as e:
            logger.warning("Reasoning loop terminated early", error=str(e))
            status = "escalated"

        return {
            "objective": objective,
            "status": status,
            "final_plan": current_plan,
            "iterations": guard.iteration,
            "corrections_applied": corrections_applied,
            "audit_report": guard.get_audit_report(),
        }

