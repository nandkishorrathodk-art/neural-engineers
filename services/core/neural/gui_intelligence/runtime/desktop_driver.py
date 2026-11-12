"""Container-bound graphical desktop driver for ANCOS GUI execution.

The observe-ground-act loop must act on the *employee's* isolated desktop, never
on the core API host. The employee container owns the X display (``:1``) and the
perception/motor binaries (``scrot`` for raster capture, ``xdotool`` for pointer
and keyboard events); the core API orchestrates by running those binaries inside
the container through the restricted Docker broker.

This module exposes low-level primitives only. Human-like cosine-eased motion is
owned by :class:`~neural.gui_intelligence.action.mouse_controller.MouseController`,
so the primitives here move the cursor in a single step to avoid double easing.

Nothing here uses the DOM, accessibility trees, Playwright/Selenium, or hidden
CLI shortcuts: perception is raster pixels and action is OS-level pointer and
keyboard events, exactly as the ANCOS strict GUI contract requires. When the
container, the X display, or a required binary is unavailable the driver raises
:class:`DesktopRuntimeUnavailable` instead of silently faking a desktop.
"""

from __future__ import annotations

import base64
import hashlib
import shlex
import time
from typing import Any

from neural.gui_intelligence.models import ScreenFrame
from neural.gui_intelligence.runtime.errors import DesktopRuntimeUnavailable
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.runtime.desktop_driver")

# A 1280x800 PNG screenshot is well under 1 MB; the ceiling only guards against a
# pathological desktop flooding the broker response.
MAX_CAPTURE_BYTES = 24_000_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ContainerDesktopDriver:
    """Drives a live employee container desktop through the restricted broker.

    All perception and motor commands run inside ``docker_id`` (as uid 1000),
    never on the host. Every method is synchronous because the GUI execution
    loop is synchronous; callers run the whole loop in a worker thread so the
    event loop is never blocked.
    """

    def __init__(
        self,
        container_manager: Any,
        docker_id: str,
        display: str = ":1",
        workspace: str = "/home/employee/workspace",
    ) -> None:
        self.container_manager = container_manager
        self.docker_id = docker_id
        self.display = display
        self.workspace = workspace

    # -- low level ---------------------------------------------------------------

    def _exec(self, command: list[str]) -> dict[str, Any]:
        return self.container_manager.execute_command_sync(
            self.docker_id, command, workdir=self.workspace
        )

    def _shell(self, script: str) -> dict[str, Any]:
        return self._exec(["sh", "-lc", script])

    def assert_available(self) -> None:
        """Fail loudly when the container, X display, or required binaries are absent."""
        probe = self._shell(
            "command -v scrot >/dev/null 2>&1 && command -v xdotool >/dev/null 2>&1 "
            f"&& DISPLAY={shlex.quote(self.display)} xdotool getdisplaygeometry >/dev/null 2>&1"
        )
        if probe.get("exit_code") != 0:
            raise DesktopRuntimeUnavailable(
                f"Employee desktop '{self.docker_id}' is not drivable "
                f"(missing scrot/xdotool or unreachable X display {self.display})."
            )

    @property
    def available(self) -> bool:
        try:
            self.assert_available()
            return True
        except DesktopRuntimeUnavailable:
            return False

    # -- capture -----------------------------------------------------------------

    def capture_png_bytes(self) -> bytes:
        """Return the current root-window pixels as PNG bytes."""
        result = self._shell("scrot - | base64 -w0")
        if result.get("exit_code") != 0:
            raise DesktopRuntimeUnavailable(
                f"Screen capture failed on '{self.docker_id}': {str(result.get('stderr', '')).strip()}"
            )
        encoded = str(result.get("stdout", "")).strip()
        if not encoded or len(encoded) > MAX_CAPTURE_BYTES:
            raise DesktopRuntimeUnavailable("Captured screenshot is empty or exceeds the size limit.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise DesktopRuntimeUnavailable("Captured screenshot payload was not valid base64.") from exc
        if not data.startswith(PNG_MAGIC):
            raise DesktopRuntimeUnavailable("Captured screenshot was not a PNG image.")
        return data

    def capture_frame(self) -> ScreenFrame:
        import io

        import numpy as np
        from PIL import Image

        start = time.perf_counter()
        data = self.capture_png_bytes()
        with Image.open(io.BytesIO(data)) as image:
            pixels = np.asarray(image.convert("RGB"), dtype=np.uint8)
        height, width = pixels.shape[:2]
        return ScreenFrame(
            timestamp=time.time(),
            resolution=(int(width), int(height)),
            pixels=pixels,
            frame_hash=hashlib.sha256(pixels.tobytes()).hexdigest(),
            capture_latency_ms=(time.perf_counter() - start) * 1000.0,
        )

    # -- motor -------------------------------------------------------------------

    def _xdotool(self, *args: Any) -> dict[str, Any]:
        argv = " ".join(shlex.quote(str(arg)) for arg in args)
        result = self._shell(f"DISPLAY={shlex.quote(self.display)} xdotool {argv}")
        if result.get("exit_code") != 0:
            raise DesktopRuntimeUnavailable(
                f"xdotool {args[0] if args else ''} failed: {str(result.get('stderr', '')).strip()}"
            )
        return result

    def cursor_position(self) -> tuple[int, int]:
        result = self._xdotool("getmouselocation", "--shell")
        values: dict[str, int] = {}
        for line in str(result.get("stdout", "")).splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                try:
                    values[key.strip()] = int(value.strip())
                except ValueError:
                    continue
        return values.get("X", 0), values.get("Y", 0)

    def move_cursor(self, x: int, y: int) -> None:
        """Move the pointer to (x, y) in a single step (easing is the caller's job).

        Deliberately not ``--sync``: xdotool blocks waiting for a pointer-motion
        event that never arrives when the cursor is already at the target, which
        would wedge the worker. The X server applies queued requests in order, so
        a following click still lands at the new position.
        """
        self._xdotool("mousemove", str(int(x)), str(int(y)))

    def button_click(self, button: int = 1) -> None:
        self._xdotool("click", str(int(button)))

    def button_down(self, button: int = 1) -> None:
        self._xdotool("mousedown", str(int(button)))

    def button_up(self, button: int = 1) -> None:
        self._xdotool("mouseup", str(int(button)))

    def scroll(self, delta: int) -> None:
        button = "5" if delta >= 0 else "4"
        for _ in range(max(1, abs(int(delta)))):
            self._xdotool("click", button)

    def type_text(self, text: str, delay_ms: int = 40) -> None:
        if text:
            self._xdotool("type", "--delay", str(int(delay_ms)), "--", text)

    def type_char(self, char: str) -> None:
        if char:
            self._xdotool("type", "--delay", "0", "--", char)

    def key(self, hotkey: str) -> None:
        self._xdotool("key", "--clearmodifiers", hotkey.strip())


__all__ = ["ContainerDesktopDriver", "MAX_CAPTURE_BYTES", "PNG_MAGIC"]
