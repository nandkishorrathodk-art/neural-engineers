"""Real-desktop capture and motor adapters for the GUI execution loop.

These adapters reuse the existing :class:`MouseController` / :class:`KeyboardController`
motion models (cosine-eased movement, human typing jitter) but change the *sink*:
instead of painting into an in-memory synthetic buffer, every motor event is
dispatched to the employee container's live X display through
:class:`ContainerDesktopDriver`. Perception reads the container's real framebuffer.

Cosine easing stays in ``MouseController``; the driver's cursor primitives move in
a single step, so pointer motion is eased exactly once.
"""

from __future__ import annotations

from typing import Any

from neural.gui_intelligence.action.keyboard_controller import KeyboardController
from neural.gui_intelligence.action.mouse_controller import MouseController
from neural.gui_intelligence.models import ScreenFrame
from neural.gui_intelligence.runtime.desktop_driver import ContainerDesktopDriver
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.runtime.motor")


class RealDesktopCaptureEngine:
    """Captures genuine framebuffer pixels from the employee container's X display."""

    def __init__(self, driver: ContainerDesktopDriver) -> None:
        self.driver = driver

    def capture_frame(self) -> ScreenFrame:
        return self.driver.capture_frame()

    def apply_visual_action(self, *_args: Any, **_kwargs: Any) -> None:
        """No-op: a real desktop updates its own pixels; nothing is synthesized."""
        return None


def build_real_dispatch(driver: ContainerDesktopDriver):
    """Return a motor dispatch function that drives the container's X display."""

    def dispatch(event: str, payload: dict) -> None:
        if event == "MOUSE_MOVE":
            driver.move_cursor(payload["x"], payload["y"])
        elif event == "MOUSE_CLICK":
            driver.button_click(payload.get("button", 1))
        elif event == "MOUSE_DOUBLE_CLICK":
            driver.button_click(payload.get("button", 1))
            driver.button_click(payload.get("button", 1))
        elif event == "MOUSE_RIGHT_CLICK":
            driver.button_click(3)
        elif event == "MOUSE_DOWN":
            driver.button_down(payload.get("button", 1))
        elif event == "MOUSE_UP":
            driver.button_up(payload.get("button", 1))
        elif event == "MOUSE_SCROLL":
            driver.scroll(payload.get("delta", 1))
        elif event == "KEY_TYPE":
            driver.type_char(payload.get("char", ""))
        elif event == "KEY_HOTKEY":
            driver.key(str(payload.get("hotkey", "Return")))
        else:
            logger.warning("Unhandled real-desktop motor event", event=event)

    return dispatch


def build_real_motor_controllers(
    driver: ContainerDesktopDriver,
    screen_resolution: tuple[int, int] = (1280, 800),
) -> tuple[MouseController, KeyboardController]:
    """Build mouse/keyboard controllers whose motion is dispatched to the live desktop."""
    dispatch = build_real_dispatch(driver)
    return (
        MouseController(screen_resolution=screen_resolution, dispatch_fn=dispatch),
        KeyboardController(dispatch_fn=dispatch),
    )


__all__ = [
    "RealDesktopCaptureEngine",
    "build_real_dispatch",
    "build_real_motor_controllers",
]
