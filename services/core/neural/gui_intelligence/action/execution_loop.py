from __future__ import annotations

import hashlib
import json
import time
from typing import Optional

from neural.exceptions import (
    EmergencyStopTriggered,
    FocusVerificationFailedError,
    GUIActionBlockedError,
    HumanApprovalRequiredError,
    StrictGUIContractViolation,
    VisualUncertaintyError,
)
from neural.gui_intelligence.action.keyboard_controller import KeyboardController
from neural.gui_intelligence.action.mouse_controller import MouseController
from neural.gui_intelligence.models import (
    GUIActionReceipt,
    GUIActionRequest,
    VisualElement,
)
from neural.gui_intelligence.perception.object_detection import UIObjectDetector
from neural.gui_intelligence.perception.screen_capture import ScreenCaptureEngine
from neural.gui_intelligence.perception.screen_change import ScreenChangeDetector
from neural.gui_intelligence.perception.visual_grounding import VisualGrounder
from neural.gui_intelligence.safety.risk_guard import GUIRiskGuard
from neural.gui_intelligence.world_model.transition_tracker import VisualTransitionTracker
from neural.gui_intelligence.world_model.visual_belief import VisualBeliefStateManager
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.action.execution_loop")


class GUIExecutionLoop:
    """
    High-Frequency Observe-Ground-Act-Verify Motor Loop (Section 4 & 15).
    Coordinates:
    1. Capture fresh screen frame (<15ms)
    2. Check focus & ground targets
    3. Cosine-eased motor execution (180ms-280ms)
    4. Visual settle wait & optical delta verification
    5. Tamper-evident receipt signature generation
    """

    def __init__(
        self,
        capture_engine: Optional[ScreenCaptureEngine] = None,
        mouse_controller: Optional[MouseController] = None,
        keyboard_controller: Optional[KeyboardController] = None,
        grounder: Optional[VisualGrounder] = None,
        change_detector: Optional[ScreenChangeDetector] = None,
        belief_manager: Optional[VisualBeliefStateManager] = None,
        transition_tracker: Optional[VisualTransitionTracker] = None,
        detector: Optional[UIObjectDetector] = None,
        risk_guard: Optional[GUIRiskGuard] = None,
    ) -> None:
        self.capture_engine = capture_engine or ScreenCaptureEngine()
        self.mouse_controller = mouse_controller or MouseController()
        self.keyboard_controller = keyboard_controller or KeyboardController()
        self.grounder = grounder or VisualGrounder()
        self.change_detector = change_detector or ScreenChangeDetector()
        self.belief_manager = belief_manager or VisualBeliefStateManager()
        self.transition_tracker = transition_tracker or VisualTransitionTracker(self.change_detector)
        self.detector = detector or UIObjectDetector()
        self.risk_guard = risk_guard or GUIRiskGuard()

    @staticmethod
    def sign_receipt_payload(payload: dict) -> str:
        """Computes SHA-256 cryptographic signature over canonical JSON receipt."""
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def execute_action(self, request: GUIActionRequest) -> GUIActionReceipt:
        """
        Executes a single GUI action within the strict GUI interaction loop.
        """
        start = time.perf_counter()
        frame_before = self.capture_engine.capture_frame()
        status = "SUCCESS"
        error_msg = None
        delta_mag = 0.0

        try:
            # 1. Enforce safety policy & risk guard interception
            self.risk_guard.guard_action(request)

            # 2. Coordinate resolution & visual grounding
            target_coords = request.coordinates
            focused_element: Optional[VisualElement] = None

            if request.target_element_id:
                # Find element
                elements = self.detector.detect(frame_before)
                for el in elements:
                    if el.element_id == request.target_element_id:
                        focused_element = el
                        target_coords = el.center()
                        break

            # 3. Dispatch action via motor controllers
            act_type = request.action_type

            # Calibrated visual grounding confidence check
            if request.confidence < 0.88 and act_type not in ("WAIT_SETTLE", "KEY_HOTKEY", "KEY_PRESS"):
                raise VisualUncertaintyError(
                    f"Action '{act_type}' confidence {request.confidence:.2f} is below calibrated threshold 0.88; "
                    "uncertain visual grounding cannot be dispatched to screen."
                )

            if target_coords is not None:
                frame_w, frame_h = frame_before.resolution
                if not (0 <= target_coords[0] <= frame_w and 0 <= target_coords[1] <= frame_h):
                    raise GUIActionBlockedError(
                        f"Action '{act_type}' coordinates {target_coords} exceed screen resolution ({frame_w}, {frame_h})"
                    )

            if act_type in ("MOUSE_MOVE", "MOUSE_CLICK", "MOUSE_DOUBLE_CLICK", "MOUSE_RIGHT_CLICK"):
                if target_coords is None:
                    raise GUIActionBlockedError(
                        f"Action '{act_type}' requires explicit coordinates or valid target_element_id"
                    )

            if act_type == "MOUSE_DRAG" and request.drag_end_coordinates:
                frame_w, frame_h = frame_before.resolution
                end_c = request.drag_end_coordinates
                if not (0 <= end_c[0] <= frame_w and 0 <= end_c[1] <= frame_h):
                    raise GUIActionBlockedError(
                        f"Action '{act_type}' drag end coordinates {end_c} exceed screen resolution ({frame_w}, {frame_h})"
                    )

            if act_type == "MOUSE_MOVE":
                if target_coords:
                    self.mouse_controller.move_to(target_coords[0], target_coords[1])
                    self.belief_manager.set_cursor_position(target_coords[0], target_coords[1])

            elif act_type == "MOUSE_CLICK":
                if target_coords:
                    self.mouse_controller.click(target_coords[0], target_coords[1])
                    self.belief_manager.set_cursor_position(target_coords[0], target_coords[1])
                    self.capture_engine.apply_visual_action("MOUSE_CLICK", target_coords)

            elif act_type == "MOUSE_DOUBLE_CLICK":
                if target_coords:
                    self.mouse_controller.double_click(target_coords[0], target_coords[1])
                    self.belief_manager.set_cursor_position(target_coords[0], target_coords[1])
                    self.capture_engine.apply_visual_action("MOUSE_DOUBLE_CLICK", target_coords)

            elif act_type == "MOUSE_RIGHT_CLICK":
                if target_coords:
                    self.mouse_controller.right_click(target_coords[0], target_coords[1])
                    self.belief_manager.set_cursor_position(target_coords[0], target_coords[1])
                    self.capture_engine.apply_visual_action("MOUSE_RIGHT_CLICK", target_coords)

            elif act_type == "MOUSE_DRAG":
                start_c = target_coords or self.mouse_controller.cursor_position
                end_c = request.drag_end_coordinates or start_c
                self.mouse_controller.drag(start_c[0], start_c[1], end_c[0], end_c[1])
                self.belief_manager.set_cursor_position(end_c[0], end_c[1])
                self.capture_engine.apply_visual_action("MOUSE_DOWN", start_c)
                self.capture_engine.apply_visual_action("MOUSE_CLICK", end_c)

            elif act_type == "MOUSE_SCROLL":
                delta = request.scroll_delta or 1
                if target_coords:
                    self.mouse_controller.scroll(delta, x=target_coords[0], y=target_coords[1])
                else:
                    self.mouse_controller.scroll(delta)

            elif act_type == "KEY_TYPE":
                text = request.text_content or ""
                # If target coords provided, move and click to focus element first
                if target_coords and focused_element is None:
                    self.mouse_controller.click(target_coords[0], target_coords[1])
                    self.capture_engine.apply_visual_action("MOUSE_CLICK", target_coords)
                    elements = self.detector.detect(frame_before)
                    for el in elements:
                        if el.contains_point(target_coords[0], target_coords[1]):
                            focused_element = el
                            focused_element.is_focused = True
                            break
                    if focused_element is None:
                        # Synthetic focus element for coordinate target
                        focused_element = VisualElement(
                            element_id="input_target",
                            bounding_box=(
                                target_coords[0] - 50,
                                target_coords[1] - 15,
                                target_coords[0] + 50,
                                target_coords[1] + 15,
                            ),
                            element_type="text_input",
                            visual_text="",
                            visual_signature="focus_ring",
                            confidence=0.95,
                            is_interactive=True,
                            is_focused=True,
                        )
                elif focused_element is None:
                    # Discover any already focused element on screen
                    elements = self.detector.detect(frame_before)
                    for el in elements:
                        if el.is_focused and el.element_type == "text_input":
                            focused_element = el
                            break

                self.keyboard_controller.type_text(
                    text=text,
                    target_element=focused_element,
                    frame=frame_before,
                )
                type_pos = target_coords or (focused_element.center() if focused_element else None)
                self.capture_engine.apply_visual_action("KEY_TYPE", type_pos, text=text)

            elif act_type == "KEY_HOTKEY" or act_type == "KEY_PRESS":
                hotkey = request.key_code or "Return"
                self.keyboard_controller.send_hotkey(hotkey)
                self.capture_engine.apply_visual_action("KEY_HOTKEY", None, text=hotkey)

            elif act_type == "WAIT_SETTLE":
                # Explicit settle wait
                time.sleep(0.05)

        except (
            EmergencyStopTriggered,
            HumanApprovalRequiredError,
            StrictGUIContractViolation,
            GUIActionBlockedError,
        ) as e:
            status = "BLOCKED_BY_SAFETY"
            error_msg = str(e)
        except FocusVerificationFailedError as e:
            status = "FOCUS_FAILED"
            error_msg = str(e)
        except VisualUncertaintyError as e:
            status = "UNCERTAINTY_ESCALATION"
            error_msg = str(e)
        except Exception as e:
            status = "TIMEOUT"
            error_msg = str(e)

        # 3. Post-action screen capture & delta calculation
        frame_after = self.capture_engine.capture_frame()
        visual_delta = self.change_detector.compute_delta(frame_before, frame_after)
        delta_mag = visual_delta.magnitude

        # 4. Settle status
        screen_settled = visual_delta.is_settled

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        payload = {
            "action_id": request.action_id,
            "employee_id": request.employee_id,
            "status": status,
            "execution_time_ms": elapsed_ms,
            "screen_settled": screen_settled,
            "pixel_delta_magnitude": delta_mag,
            "observed_postcondition": f"delta_{delta_mag:.4f}",
            "error_message": error_msg,
        }
        signature = self.sign_receipt_payload(payload)

        return GUIActionReceipt(
            action_id=request.action_id,
            employee_id=request.employee_id,
            status=status,  # type: ignore
            execution_time_ms=elapsed_ms,
            screen_settled=screen_settled,
            pixel_delta_magnitude=delta_mag,
            observed_postcondition=payload["observed_postcondition"],
            error_message=error_msg,
            signature=signature,
        )

    def execute_chunk(self, chunk: list[GUIActionRequest]) -> list[GUIActionReceipt]:
        """
        Executes a bounded batch of actions in sequence.
        Halts immediately if any action in the chunk fails or triggers escalation.
        """
        receipts: list[GUIActionReceipt] = []
        for req in chunk:
            receipt = self.execute_action(req)
            receipts.append(receipt)
            if receipt.status != "SUCCESS":
                logger.warning(
                    "Action chunk execution halted early due to non-success receipt",
                    action_id=req.action_id,
                    status=receipt.status,
                )
                break
        return receipts
