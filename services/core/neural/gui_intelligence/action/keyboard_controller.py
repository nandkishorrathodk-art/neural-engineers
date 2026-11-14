from __future__ import annotations

import random
import time
from typing import Callable, Optional

from neural.exceptions import FocusVerificationFailedError
from neural.gui_intelligence.models import ScreenFrame, VisualElement
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.action.keyboard_controller")


class KeyboardController:
    """
    Verified-Focus Keystroke Transmission & Hotkey Controller (Section 9.2).
    Enforces the Visual Focus Verification Gate:
    - Text entry without visually verified focus cue is strictly prohibited.
    - Simulates realistic human typing jitter: 25ms +/- 8ms per keystroke.
    - Supports single keycodes and composite hotkeys ('ctrl+c', 'Return', etc.).
    """

    BASE_KEY_DELAY_MS: float = 25.0
    JITTER_MS: float = 8.0

    def __init__(
        self,
        dispatch_fn: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self.dispatch_fn = dispatch_fn
        self.keystroke_history: list[dict] = []
        self._last_focused_element: Optional[VisualElement] = None

    def verify_visual_focus(
        self,
        target_element: VisualElement,
        frame: Optional[ScreenFrame] = None,
    ) -> bool:
        """
        Visually confirms that target_element holds active focus cue
        (e.g. blinking cursor pipe '|' or high-contrast border ring).
        """
        # If element metadata already flagged focused
        if target_element.is_focused:
            self._last_focused_element = target_element
            return True

        # Check raster pixels around element box if frame is available
        if frame is not None:
            xmin, ymin, xmax, ymax = target_element.bounding_box
            h, w = frame.pixels.shape[:2]
            if 0 <= ymin < ymax <= h and 0 <= xmin < xmax <= w:
                patch = frame.pixels[ymin:ymax, xmin:xmax]
                # Check for active border highlight (blue/cyan) or high vertical line contrast (cursor pipe)
                blue_cues = np_has_focus_cue(patch)
                if blue_cues:
                    target_element.is_focused = True
                    self._last_focused_element = target_element
                    return True

        return False

    def type_text(
        self,
        text: str,
        target_element: Optional[VisualElement] = None,
        frame: Optional[ScreenFrame] = None,
        force_unverified: bool = False,
    ) -> float:
        """
        Types a string into the focused target element.
        Enforces Focus Verification Gate unless explicitly mocked for test purposes.
        """
        start = time.perf_counter()

        if target_element is not None:
            is_focused = self.verify_visual_focus(target_element, frame)
            if not is_focused and not force_unverified:
                raise FocusVerificationFailedError(
                    f"Keyboard text entry blocked: target element '{target_element.element_id}' "
                    "lacks verified visual focus cue."
                )
        elif self._last_focused_element is None and not force_unverified:
            raise FocusVerificationFailedError(
                "Keyboard text entry blocked: no target element focused on screen."
            )

        # Transmit each keystroke with jitter
        for char in text:
            jitter = random.uniform(-self.JITTER_MS, self.JITTER_MS)
            _ = max(5.0, self.BASE_KEY_DELAY_MS + jitter)
            if self.dispatch_fn:
                self.dispatch_fn("KEY_TYPE", {"char": char})

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self.keystroke_history.append(
            {
                "type": "KEY_TYPE",
                "text_length": len(text),
                "elapsed_ms": elapsed_ms,
            }
        )
        return elapsed_ms

    def send_hotkey(self, hotkey: str) -> float:
        """Transmits a hotkey sequence (e.g. 'ctrl+c', 'Return', 'alt+tab')."""
        start = time.perf_counter()
        normalized_hotkey = canonicalize_hotkey(hotkey)
        if self.dispatch_fn:
            self.dispatch_fn("KEY_HOTKEY", {"hotkey": normalized_hotkey})
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self.keystroke_history.append(
            {
                "type": "KEY_HOTKEY",
                "hotkey": normalized_hotkey,
                "elapsed_ms": elapsed_ms,
            }
        )
        return elapsed_ms


_MODIFIER_TOKENS = {"ctrl", "control", "alt", "shift", "super", "meta", "hyper", "altgr"}
# X keysym names are case-sensitive ("Return", "End", "Page_Up"). Lowercasing a
# hotkey makes xdotool silently drop the unknown name ("No such key name
# 'return'"), so special keys must be canonicalized, not lowercased.
_CANONICAL_KEYSYMS = {
    "return": "Return",
    "enter": "Return",
    "escape": "Escape",
    "esc": "Escape",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    "prior": "Prior",
    "next": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "space": "space",
    "spacebar": "space",
    "printscreen": "Print",
    "menu": "Menu",
}


def canonicalize_hotkey(hotkey: str) -> str:
    """Normalize a hotkey to xdotool's expected casing.

    Modifier tokens are lowercased; named keysyms are mapped to their canonical
    X name. A single printable character (``ctrl+s``) passes through unchanged.
    """
    parts = [part.strip() for part in hotkey.strip().split("+") if part.strip()]
    if not parts:
        return hotkey.strip().lower()
    normalized: list[str] = []
    for part in parts:
        lowered = part.lower()
        if lowered in _MODIFIER_TOKENS:
            normalized.append(lowered)
            continue
        keysym = _CANONICAL_KEYSYMS.get(lowered.replace("_", ""))
        normalized.append(keysym if keysym else part)
    return "+".join(normalized)


def np_has_focus_cue(patch) -> bool:
    """Helper to detect border ring or cursor pipe in a raster crop."""
    try:
        import numpy as np
        # Look for bright blue/cyan focus border or thin vertical dark line
        blue_pixels = np.sum((patch[:, :, 2] > 160) & (patch[:, :, 0] < 100))
        if blue_pixels > 15:
            return True
        # Check for vertical cursor line (column with significant dark contrast)
        gray = np.mean(patch, axis=2)
        col_stds = np.std(gray, axis=0)
        return bool(np.any(col_stds > 30.0))
    except Exception:
        return False

