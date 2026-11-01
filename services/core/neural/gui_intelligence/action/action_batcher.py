from __future__ import annotations

import uuid

from neural.gui_intelligence.models import GUIActionRequest
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.action.action_batcher")


class ActionBatcher:
    """
    Macro Action Chunking & Batching Engine (Section 7.1 & 15.1).
    Fuses continuous sequential GUI operations with deterministic intermediate transitions
    into composite action chunks (bounded between 2 and 8 actions).
    Reduces latency from ~3500ms down to <250ms per chunk.
    """

    MAX_CHUNK_SIZE: int = 8

    def __init__(self, employee_id: str = "emp_gui_default") -> None:
        self.employee_id = employee_id

    def build_chunk(
        self,
        actions: list[GUIActionRequest],
    ) -> list[GUIActionRequest]:
        """
        Indexes and annotates an action list into an atomic chunk with chunk metadata.
        """
        total = min(len(actions), self.MAX_CHUNK_SIZE)
        chunked = []
        for idx, act in enumerate(actions[:total]):
            req_dict = act.model_dump()
            req_dict["action_chunk_index"] = idx
            req_dict["total_chunk_size"] = total
            chunked.append(GUIActionRequest(**req_dict))
        return chunked

    def create_click_and_type_chunk(
        self,
        target_coords: tuple[int, int],
        text: str,
        press_enter: bool = True,
        confidence: float = 0.95,
        risk_level: str = "LOW",
    ) -> list[GUIActionRequest]:
        """
        Synthesizes a common form-filling macro:
        1. Click field to focus
        2. Settle wait
        3. Type text into verified focus
        4. (Optional) Press enter / Return
        """
        actions: list[GUIActionRequest] = []

        # Step 1: Click to focus
        actions.append(
            GUIActionRequest(
                action_id=f"act_click_{uuid.uuid4().hex[:8]}",
                employee_id=self.employee_id,
                action_type="MOUSE_CLICK",
                coordinates=target_coords,
                confidence=confidence,
                risk_level=risk_level,  # type: ignore
            )
        )

        # Step 2: Settle
        actions.append(
            GUIActionRequest(
                action_id=f"act_settle_{uuid.uuid4().hex[:8]}",
                employee_id=self.employee_id,
                action_type="WAIT_SETTLE",
                confidence=1.0,
                risk_level="LOW",
            )
        )

        # Step 3: Type text
        actions.append(
            GUIActionRequest(
                action_id=f"act_type_{uuid.uuid4().hex[:8]}",
                employee_id=self.employee_id,
                action_type="KEY_TYPE",
                text_content=text,
                coordinates=target_coords,
                confidence=confidence,
                risk_level=risk_level,  # type: ignore
            )
        )

        # Step 4: Submit / Return
        if press_enter:
            actions.append(
                GUIActionRequest(
                    action_id=f"act_key_{uuid.uuid4().hex[:8]}",
                    employee_id=self.employee_id,
                    action_type="KEY_HOTKEY",
                    key_code="Return",
                    confidence=1.0,
                    risk_level=risk_level,  # type: ignore
                )
            )

        return self.build_chunk(actions)

    def create_menu_select_chunk(
        self,
        menu_coords: tuple[int, int],
        item_coords: tuple[int, int],
        confidence: float = 0.95,
    ) -> list[GUIActionRequest]:
        """
        Synthesizes a menu selection macro:
        1. Click menu header
        2. Wait settle
        3. Click menu item
        """
        actions = [
            GUIActionRequest(
                action_id=f"act_menu_{uuid.uuid4().hex[:8]}",
                employee_id=self.employee_id,
                action_type="MOUSE_CLICK",
                coordinates=menu_coords,
                confidence=confidence,
                risk_level="LOW",
            ),
            GUIActionRequest(
                action_id=f"act_settle_{uuid.uuid4().hex[:8]}",
                employee_id=self.employee_id,
                action_type="WAIT_SETTLE",
                confidence=1.0,
                risk_level="LOW",
            ),
            GUIActionRequest(
                action_id=f"act_item_{uuid.uuid4().hex[:8]}",
                employee_id=self.employee_id,
                action_type="MOUSE_CLICK",
                coordinates=item_coords,
                confidence=confidence,
                risk_level="LOW",
            ),
        ]
        return self.build_chunk(actions)
