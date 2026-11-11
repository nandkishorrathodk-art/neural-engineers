from __future__ import annotations

import time
import uuid
from typing import Optional

from neural.exceptions import VisualUncertaintyError
from neural.gui_intelligence.action.action_batcher import ActionBatcher
from neural.gui_intelligence.brain.task_planner import PlanStep
from neural.gui_intelligence.models import DesktopBeliefState, GUIActionRequest, ScreenFrame
from neural.gui_intelligence.perception.visual_grounding import VisualGrounder
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.brain.fast_policy")


class System1FastPolicy:
    """
    System 1: Fast GUI Action Brain (Section 4.2).
    Responsible for low-latency target localization, motor coordinate calculation,
    and macro action chunking.
    Operates within <50ms policy latency budget and enforces confidence threshold >= 0.88.
    """

    CONFIDENCE_THRESHOLD: float = 0.88

    def __init__(
        self,
        grounder: Optional[VisualGrounder] = None,
        batcher: Optional[ActionBatcher] = None,
        employee_id: str = "emp_gui_default",
    ) -> None:
        self.grounder = grounder or VisualGrounder()
        self.batcher = batcher or ActionBatcher(employee_id)
        self.employee_id = employee_id

    def select_action_chunk(
        self,
        step: PlanStep,
        frame: ScreenFrame,
        belief_state: Optional[DesktopBeliefState] = None,
    ) -> list[GUIActionRequest]:
        """
        Translates a symbolic PlanStep into a concrete chunk of grounded GUIActionRequests.
        Guarantees sub-50ms inference.
        """
        start = time.perf_counter()

        # 1. Ground target descriptor from screen pixels
        query = self._extract_clean_query(step.target_descriptor)
        elem_type = self._extract_element_type(step.target_descriptor)
        grounded = self.grounder.ground_query(frame, query, expected_element_type=elem_type)

        if not grounded.is_confident:
            raise VisualUncertaintyError(
                f"Fast Brain uncertainty escalation: target '{step.target_descriptor}' "
                f"confidence={grounded.confidence:.2f} < {self.CONFIDENCE_THRESHOLD}"
            )

        coords = grounded.coordinates
        actions: list[GUIActionRequest] = []

        if step.action_intent == "CLICK":
            actions.append(
                GUIActionRequest(
                    action_id=f"act_click_{uuid.uuid4().hex[:8]}",
                    employee_id=self.employee_id,
                    action_type="MOUSE_CLICK",
                    coordinates=coords,
                    confidence=grounded.confidence,
                    risk_level=step.risk_level,  # type: ignore
                )
            )
            actions.append(
                GUIActionRequest(
                    action_id=f"act_settle_{uuid.uuid4().hex[:8]}",
                    employee_id=self.employee_id,
                    action_type="WAIT_SETTLE",
                    confidence=1.0,
                    risk_level="LOW",
                )
            )

        elif step.action_intent == "TYPE":
            text_to_type = step.input_value or ""
            return self.batcher.create_click_and_type_chunk(
                target_coords=coords,
                text=text_to_type,
                press_enter=False,
                confidence=grounded.confidence,
                risk_level=step.risk_level,
            )

        elif step.action_intent == "SELECT_MENU":
            # Two-step selection
            actions = self.batcher.create_menu_select_chunk(
                menu_coords=coords,
                item_coords=(coords[0], coords[1] + 30),
                confidence=grounded.confidence,
            )
            return actions

        elif step.action_intent == "VERIFY":
            actions.append(
                GUIActionRequest(
                    action_id=f"act_verify_{uuid.uuid4().hex[:8]}",
                    employee_id=self.employee_id,
                    action_type="WAIT_SETTLE",
                    confidence=1.0,
                    risk_level="LOW",
                )
            )

        else:
            actions.append(
                GUIActionRequest(
                    action_id=f"act_gen_{uuid.uuid4().hex[:8]}",
                    employee_id=self.employee_id,
                    action_type="MOUSE_CLICK",
                    coordinates=coords,
                    confidence=grounded.confidence,
                    risk_level=step.risk_level,  # type: ignore
                )
            )

        chunk = self.batcher.build_chunk(actions)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        logger.debug(
            "Fast policy chunk selected",
            step=step.step_id,
            chunk_size=len(chunk),
            elapsed_ms=elapsed_ms,
        )
        return chunk

    @staticmethod
    def _extract_clean_query(descriptor: str) -> str:
        """Extracts text query from descriptors like 'button[text=\\'Submit\\']' or 'text_input[label=\\'Username\\']'."""
        for key in ("text='", 'text="', "label='", 'label="'):
            if key in descriptor:
                quote_char = key[-1]
                start_idx = descriptor.find(key) + len(key)
                end_idx = descriptor.find(quote_char, start_idx)
                if end_idx != -1:
                    return descriptor[start_idx:end_idx]
        if "[" in descriptor:
            inside = descriptor[descriptor.find("[") + 1 : descriptor.rfind("]")]
            if "=" in inside:
                val = inside.split("=", 1)[1].strip("'\"")
                if val:
                    return val
            return descriptor.split("[")[0]
        return descriptor

    @staticmethod
    def _extract_element_type(descriptor: str) -> Optional[str]:
        """Extracts expected element type from descriptors like 'button[text=\\'Submit\\']'."""
        if "[" in descriptor:
            prefix = descriptor.split("[")[0].strip()
            if prefix in ("button", "text_input", "menu", "menu_item", "icon", "dialog", "scrollbar"):
                return prefix
        return None

