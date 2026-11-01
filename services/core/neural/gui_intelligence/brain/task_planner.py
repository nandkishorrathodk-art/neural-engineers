from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from neural.gui_intelligence.models import DesktopBeliefState
from neural.llm.gateway import LLMGateway
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.brain.task_planner")


@dataclass
class PlanStep:
    step_id: str
    phase_name: str
    action_intent: str  # e.g. "CLICK", "TYPE", "SELECT_MENU", "VERIFY"
    target_descriptor: str  # e.g. "button[text='Save']", "text_input[label='Username']"
    input_value: Optional[str] = None
    expected_precondition: Optional[str] = None
    expected_postcondition: Optional[str] = None
    risk_level: str = "LOW"

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "phase_name": self.phase_name,
            "action_intent": self.action_intent,
            "target_descriptor": self.target_descriptor,
            "input_value": self.input_value,
            "expected_precondition": self.expected_precondition,
            "expected_postcondition": self.expected_postcondition,
            "risk_level": self.risk_level,
        }


@dataclass
class CompiledExecutionPlan:
    plan_id: str
    task_objective: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    planning_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_objective": self.task_objective,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "planning_latency_ms": self.planning_latency_ms,
        }


class System2TaskPlanner:
    """
    System 2: Deep Cognitive Brain Planner (Section 4.1).
    Responsible for strategic planning, hierarchical goal decomposition,
    unfamiliar UI exploration, and strategic recovery.
    Never calculates raw mouse trajectory waypoints; outputs symbolic action intents
    with visual target descriptors and invariants.
    """

    def __init__(self, llm_gateway: Optional[LLMGateway] = None) -> None:
        self.llm_gateway = llm_gateway
        self._custom_plan_generator: Optional[Any] = None

    def set_custom_plan_generator(self, gen_fn: Any) -> None:
        """Sets a mock or custom generator function for testing."""
        self._custom_plan_generator = gen_fn

    async def create_plan(
        self,
        task_objective: str,
        belief_state: Optional[DesktopBeliefState] = None,
    ) -> CompiledExecutionPlan:
        """
        Decomposes high-level task objective into structured PlanSteps.
        """
        start = time.perf_counter()
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"

        if self._custom_plan_generator:
            steps = self._custom_plan_generator(task_objective, belief_state)
            latency_ms = (time.perf_counter() - start) * 1000.0
            return CompiledExecutionPlan(
                plan_id=plan_id,
                task_objective=task_objective,
                steps=steps,
                planning_latency_ms=latency_ms,
            )

        # Rule-based / semantic goal decomposition
        steps: list[PlanStep] = []
        obj_lower = task_objective.lower()

        if "form" in obj_lower or "fill" in obj_lower or "login" in obj_lower:
            steps = [
                PlanStep(
                    step_id=f"{plan_id}_1",
                    phase_name="Focus Username",
                    action_intent="CLICK",
                    target_descriptor="text_input[label='Username']",
                    expected_postcondition="Field focused with active cursor",
                ),
                PlanStep(
                    step_id=f"{plan_id}_2",
                    phase_name="Enter Username",
                    action_intent="TYPE",
                    target_descriptor="text_input[label='Username']",
                    input_value="admin_user",
                    expected_postcondition="Text appears in field",
                ),
                PlanStep(
                    step_id=f"{plan_id}_3",
                    phase_name="Submit Form",
                    action_intent="CLICK",
                    target_descriptor="button[text='Submit']",
                    expected_postcondition="Submission processed",
                ),
            ]
        elif "save" in obj_lower or "export" in obj_lower:
            steps = [
                PlanStep(
                    step_id=f"{plan_id}_1",
                    phase_name="Open File Menu",
                    action_intent="CLICK",
                    target_descriptor="menu[text='File']",
                    expected_postcondition="Menu dropdown visible",
                ),
                PlanStep(
                    step_id=f"{plan_id}_2",
                    phase_name="Select Save",
                    action_intent="CLICK",
                    target_descriptor="menu_item[text='Save As...']",
                    expected_postcondition="Save dialog open",
                ),
                PlanStep(
                    step_id=f"{plan_id}_3",
                    phase_name="Confirm Save",
                    action_intent="CLICK",
                    target_descriptor="button[text='Save']",
                    expected_postcondition="File saved and dialog closed",
                ),
            ]
        else:
            # Generic procedural exploration
            steps = [
                PlanStep(
                    step_id=f"{plan_id}_1",
                    phase_name="Locate Primary Target",
                    action_intent="CLICK",
                    target_descriptor=f"element[text='{task_objective[:20]}']",
                    expected_postcondition="Element activated",
                ),
                PlanStep(
                    step_id=f"{plan_id}_2",
                    phase_name="Verify Completion",
                    action_intent="VERIFY",
                    target_descriptor="visual_state[settled=true]",
                    expected_postcondition="Objective verified",
                ),
            ]

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "System 2 plan generated",
            plan_id=plan_id,
            steps_count=len(steps),
            latency_ms=latency_ms,
        )

        return CompiledExecutionPlan(
            plan_id=plan_id,
            task_objective=task_objective,
            steps=steps,
            planning_latency_ms=latency_ms,
        )

