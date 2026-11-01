from __future__ import annotations

import time
from typing import Any, Optional

from neural.gui_intelligence.action.keyboard_controller import KeyboardController
from neural.gui_intelligence.action.mouse_controller import MouseController
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.recovery.state_recovery")


class GUIStateRecovery:
    """
    GUI State Recovery & Backtracking Engine (Section 18).
    Backtracks application states and restores valid baseline desktop conditions
    when actions fail or the employee enters an inconsistent application state.
    """

    def __init__(
        self,
        keyboard_controller: Optional[KeyboardController] = None,
        mouse_controller: Optional[MouseController] = None,
    ) -> None:
        self.keyboard_controller = keyboard_controller or KeyboardController()
        self.mouse_controller = mouse_controller or MouseController()
        self.recovery_history: list[dict[str, Any]] = []

    def undo_mutation(self) -> dict[str, Any]:
        """Dispatches Ctrl+Z to undo recent application keystroke or mutation."""
        start = time.perf_counter()
        self.keyboard_controller.send_hotkey("ctrl+z")
        elapsed = (time.perf_counter() - start) * 1000.0
        rec = {"strategy": "UNDO_CTRL_Z", "elapsed_ms": elapsed, "timestamp": time.time()}
        self.recovery_history.append(rec)
        return rec

    def dismiss_modal(self) -> dict[str, Any]:
        """Dispatches Escape to cancel open menus, dropdowns, or alert popups."""
        start = time.perf_counter()
        self.keyboard_controller.send_hotkey("Escape")
        elapsed = (time.perf_counter() - start) * 1000.0
        rec = {"strategy": "DISMISS_ESCAPE", "elapsed_ms": elapsed, "timestamp": time.time()}
        self.recovery_history.append(rec)
        return rec

    def reset_focus_to_desktop(self, resolution: tuple[int, int] = (1280, 800)) -> dict[str, Any]:
        """Clicks on a neutral empty background location to deselect active items."""
        start = time.perf_counter()
        neutral_x = resolution[0] - 10
        neutral_y = resolution[1] - 10
        self.mouse_controller.click(neutral_x, neutral_y)
        elapsed = (time.perf_counter() - start) * 1000.0
        rec = {
            "strategy": "RESET_NEUTRAL_FOCUS",
            "coords": (neutral_x, neutral_y),
            "elapsed_ms": elapsed,
            "timestamp": time.time(),
        }
        self.recovery_history.append(rec)
        return rec

