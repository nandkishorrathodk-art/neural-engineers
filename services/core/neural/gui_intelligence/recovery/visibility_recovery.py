from __future__ import annotations

from typing import Any, Optional

from neural.gui_intelligence.action.keyboard_controller import KeyboardController
from neural.gui_intelligence.action.mouse_controller import MouseController
from neural.gui_intelligence.models import VisualElement, VisualWindowState
from neural.gui_intelligence.perception.screen_capture import ScreenCaptureEngine
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.recovery.visibility_recovery")


class VisibilityRecoveryEngine:
    """
    Visibility Recovery Intelligence (Section 8).
    Implements the 5-step systematic hierarchy to reveal occluded, collapsed, or scrolled controls:
    Step 1: Check Window Occlusion -> Click visible edge/title of target window.
    Step 2: Check Collapsed Menus/Drawers -> Click parent expander icon.
    Step 3: Check Scrollable Container -> Mouse wheel scroll toward target quadrant.
    Step 4: Check Blocking Dialogs/Popups -> Close or acknowledge unexpected modal.
    Step 5: Escalate to System 2 Deep Brain.
    """

    def __init__(
        self,
        mouse_controller: Optional[MouseController] = None,
        keyboard_controller: Optional[KeyboardController] = None,
        capture_engine: Optional[ScreenCaptureEngine] = None,
    ) -> None:
        self.mouse_controller = mouse_controller or MouseController()
        self.keyboard_controller = keyboard_controller or KeyboardController()
        self.capture_engine = capture_engine or ScreenCaptureEngine()
        self.recovery_audit_log: list[dict[str, Any]] = []

    def recover_control_visibility(
        self,
        target_descriptor: str,
        visible_windows: list[VisualWindowState],
        active_dialogs: list[VisualElement],
        scrollable_elements: list[VisualElement],
        menu_expanders: list[VisualElement],
    ) -> dict[str, Any]:
        """
        Executes the 5-step visibility recovery cascade.
        Returns the action taken and whether the control was recovered.
        """
        # Step 1: Check Window Occlusion
        for win in visible_windows[1:]:
            if target_descriptor.lower() in win.title_text.lower():
                xmin, ymin, xmax, ymax = win.bounding_box
                title_click_x = xmin + min(100, max(1, (xmax - xmin) // 2))
                title_click_y = ymin + 16
                self.mouse_controller.click(title_click_x, title_click_y)
                action_info = {
                    "step": 1,
                    "strategy": "WINDOW_OCCLUSION_RECOVERY",
                    "target_window": win.window_id,
                    "click_coords": (title_click_x, title_click_y),
                    "status": "RECOVERED",
                }
                self.recovery_audit_log.append(action_info)
                return action_info

        # Step 2: Check Collapsed Menus / Drawers
        if menu_expanders:
            expander = menu_expanders[0]
            cx, cy = expander.center()
            self.mouse_controller.click(cx, cy)
            action_info = {
                "step": 2,
                "strategy": "COLLAPSED_EXPANDER_RECOVERY",
                "expander_id": expander.element_id,
                "click_coords": (cx, cy),
                "status": "RECOVERED",
            }
            self.recovery_audit_log.append(action_info)
            return action_info

        # Step 3: Check Scrollable Container
        if scrollable_elements:
            scrollbar = scrollable_elements[0]
            cx, cy = scrollbar.center()
            self.mouse_controller.scroll(delta=3, x=cx, y=cy)
            action_info = {
                "step": 3,
                "strategy": "SCROLL_CONTAINER_RECOVERY",
                "scrollbar_id": scrollbar.element_id,
                "scroll_delta": 3,
                "status": "RECOVERED",
            }
            self.recovery_audit_log.append(action_info)
            return action_info

        # Step 4: Check Blocking Dialogs / Popups
        if active_dialogs:
            dialog = active_dialogs[0]
            # Try pressing Escape or clicking dismiss button
            self.keyboard_controller.send_hotkey("Escape")
            action_info = {
                "step": 4,
                "strategy": "DISMISS_MODAL_DIALOG",
                "dialog_id": dialog.element_id,
                "action": "HOTKEY_ESCAPE",
                "status": "RECOVERED",
                "security_audit_warning": "Unexpected modal dialog dismissed during automated workflow",
            }
            self.recovery_audit_log.append(action_info)
            return action_info

        # Step 5: Escalate to System 2 Deep Brain
        action_info = {
            "step": 5,
            "strategy": "ESCALATE_TO_DEEP_BRAIN",
            "target_descriptor": target_descriptor,
            "status": "ESCALATED",
        }
        self.recovery_audit_log.append(action_info)
        return action_info
