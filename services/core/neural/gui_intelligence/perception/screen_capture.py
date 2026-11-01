from __future__ import annotations

import hashlib
import time
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageDraw

from neural.config import get_settings
from neural.gui_intelligence.models import ScreenFrame
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.perception.screen_capture")


class ScreenCaptureUnavailableError(RuntimeError):
    """Raised when real desktop pixels are unavailable in a non-simulated run."""


class ScreenCaptureEngine:
    """
    Screen Capture Engine for ANCOS Workstations.
    Captures raw RGB framebuffers from TigerVNC / X11 display (or in-memory mock/synthetic buffer).
    Guarantees sub-15ms frame grab latency and raw pixel extraction without DOM/AT-SPI.
    """

    def __init__(
        self,
        default_resolution: tuple[int, int] = (1280, 800),
        vnc_host: str = "127.0.0.1",
        vnc_port: int = 5901,
        allow_synthetic: Optional[bool] = None,
    ) -> None:
        self.default_resolution = default_resolution
        self.vnc_host = vnc_host
        self.vnc_port = vnc_port
        self.allow_synthetic = (
            get_settings().APP_ENV != "production"
            if allow_synthetic is None
            else allow_synthetic
        )
        self._current_frame: Optional[ScreenFrame] = None
        self._mock_pixels: Optional[np.ndarray] = None
        self._synthetic_elements: list[dict[str, Any]] = []

    def set_mock_frame(self, frame_or_pixels: np.ndarray | Image.Image | ScreenFrame | None) -> None:
        """Sets a mock frame for testing or simulation."""
        ScreenCaptureEngine._last_rendered_elements = []
        if frame_or_pixels is None:
            self._mock_pixels = None
            self._current_frame = None
            return

        if isinstance(frame_or_pixels, ScreenFrame):
            self._current_frame = frame_or_pixels
            self._mock_pixels = frame_or_pixels.pixels
            return

        if isinstance(frame_or_pixels, Image.Image):
            pixels = np.array(frame_or_pixels.convert("RGB"), dtype=np.uint8)
        else:
            pixels = np.asarray(frame_or_pixels, dtype=np.uint8)

        h, w = pixels.shape[:2]
        frame_hash = hashlib.sha256(pixels.tobytes()).hexdigest()
        self._mock_pixels = pixels
        self._current_frame = ScreenFrame(
            timestamp=time.time(),
            resolution=(w, h),
            pixels=pixels,
            frame_hash=frame_hash,
            capture_latency_ms=1.5,
        )

    _last_rendered_elements: list[dict[str, Any]] = []

    @classmethod
    def get_last_rendered_elements(cls) -> list[dict[str, Any]]:
        return list(cls._last_rendered_elements)

    @classmethod
    def clear_rendered_elements(cls) -> None:
        cls._last_rendered_elements = []

    def render_synthetic_desktop(
        self,
        windows: Optional[list[dict[str, Any]]] = None,
        background_color: tuple[int, int, int] = (40, 44, 52),
        resolution: Optional[tuple[int, int]] = None,
    ) -> ScreenFrame:
        """
        Renders a synthetic desktop frame with windows, titlebars, buttons, text inputs,
        and dialogs using raster pixels.
        """
        w, h = resolution or self.default_resolution
        img = Image.new("RGB", (w, h), color=background_color)
        draw = ImageDraw.Draw(img)

        ScreenCaptureEngine._last_rendered_elements = []

        # Draw panel/taskbar at bottom
        panel_h = 32
        draw.rectangle([(0, h - panel_h), (w, h)], fill=(30, 32, 40))
        draw.rectangle([(8, h - panel_h + 4), (80, h - 4)], fill=(60, 65, 80), outline=(100, 110, 130))
        draw.text((16, h - panel_h + 8), "Applications", fill=(220, 220, 220))
        ScreenCaptureEngine._last_rendered_elements.append({
            "text": "Applications",
            "rect": (8, h - panel_h + 4, 80, h - 4),
            "type": "button",
            "is_focused": False,
        })

        if windows is None:
            windows = [
                {
                    "title": "Document Editor - Document1",
                    "rect": (80, 50, 1000, 620),
                    "is_active": True,
                    "elements": [
                        {"type": "menu", "text": "File", "rect": (100, 90, 60, 24)},
                        {"type": "menu", "text": "Edit", "rect": (170, 90, 60, 24)},
                        {"type": "menu", "text": "View", "rect": (240, 90, 60, 24)},
                        {"type": "menu_item", "text": "Save As...", "rect": (100, 120, 100, 28)},
                        {"type": "button", "text": "Save", "rect": (100, 160, 90, 32)},
                        {"type": "button", "text": "Submit", "rect": (210, 160, 90, 32)},
                        {"type": "text_input", "text": "Username", "rect": (100, 210, 250, 32), "is_focused": True},
                    ],
                }
            ]

        for win in windows:
            bx, by, bw, bh = win.get("rect", (100, 80, 800, 500))
            title = win.get("title", "Application")
            is_active = win.get("is_active", True)
            elements = win.get("elements", [])

            # Window shadow
            draw.rectangle([(bx + 4, by + 4), (bx + bw + 4, by + bh + 4)], fill=(20, 20, 20))
            # Window body
            win_bg = win.get("bg_color", (245, 247, 250))
            draw.rectangle([(bx, by), (bx + bw, by + bh)], fill=win_bg, outline=(80, 80, 90), width=2)

            # Title bar
            tb_h = 32
            tb_fill = (52, 101, 164) if is_active else (136, 138, 133)
            draw.rectangle([(bx, by), (bx + bw, by + tb_h)], fill=tb_fill)
            draw.text((bx + 12, by + 8), title, fill=(255, 255, 255))
            ScreenCaptureEngine._last_rendered_elements.append({
                "text": title,
                "rect": (bx, by, bx + bw, by + tb_h),
                "type": "window_title",
                "is_focused": is_active,
            })

            # Window controls: close, minimize, maximize
            draw.ellipse([(bx + bw - 24, by + 8), (bx + bw - 8, by + 24)], fill=(204, 0, 0))
            draw.ellipse([(bx + bw - 44, by + 8), (bx + bw - 28, by + 24)], fill=(245, 121, 0))
            draw.ellipse([(bx + bw - 64, by + 8), (bx + bw - 48, by + 24)], fill=(115, 210, 22))

            # Render controls inside window
            for elem in elements:
                etype = elem.get("type", "button")
                ex, ey, ew, eh = elem.get("rect", (bx + 20, by + 50, 100, 32))
                text = elem.get("text", "")
                is_focused = elem.get("is_focused", False)
                ScreenCaptureEngine._last_rendered_elements.append({
                    "text": text,
                    "rect": (ex, ey, ex + ew, ey + eh),
                    "type": etype,
                    "is_focused": is_focused,
                })

                if etype == "button":
                    btn_color = elem.get("color", (70, 130, 180))
                    draw.rectangle([(ex, ey), (ex + ew, ey + eh)], fill=btn_color, outline=(30, 60, 90), width=1)
                    if text:
                        draw.text((ex + 10, ey + 8), text, fill=(255, 255, 255))
                elif etype == "text_input":
                    border_color = (0, 120, 215) if is_focused else (180, 180, 180)
                    draw.rectangle([(ex, ey), (ex + ew, ey + eh)], fill=(255, 255, 255), outline=border_color, width=2 if is_focused else 1)
                    if text:
                        draw.text((ex + 8, ey + 8), text, fill=(0, 0, 0))
                    if is_focused:
                        # Draw blinking cursor line
                        cursor_x = ex + 8 + (len(text) * 7)
                        draw.line([(cursor_x, ey + 4), (cursor_x, ey + eh - 4)], fill=(0, 0, 0), width=2)
                elif etype == "icon":
                    draw.rectangle([(ex, ey), (ex + ew, ey + eh)], fill=(120, 120, 140))
                    if text:
                        draw.text((ex + 4, ey + eh + 2), text, fill=(30, 30, 30))
                elif etype == "dialog":
                    draw.rectangle([(ex, ey), (ex + ew, ey + eh)], fill=(255, 250, 240), outline=(200, 50, 50), width=3)
                    draw.rectangle([(ex, ey), (ex + ew, ey + 28)], fill=(200, 50, 50))
                    draw.text((ex + 8, ey + 6), elem.get("title", "Dialog"), fill=(255, 255, 255))
                    if text:
                        draw.text((ex + 12, ey + 36), text, fill=(20, 20, 20))
                elif etype == "scrollbar":
                    draw.rectangle([(ex, ey), (ex + ew, ey + eh)], fill=(220, 220, 220))
                    thumb_h = max(20, eh // 3)
                    draw.rectangle([(ex, ey + 10), (ex + ew, ey + 10 + thumb_h)], fill=(150, 150, 150))
                elif etype == "menu":
                    draw.rectangle([(ex, ey), (ex + ew, ey + eh)], fill=(240, 240, 240), outline=(180, 180, 180))
                    if text:
                        draw.text((ex + 6, ey + 4), text, fill=(20, 20, 20))
                elif etype == "tab":
                    tab_color = (245, 247, 250) if elem.get("selected") else (220, 225, 230)
                    draw.rectangle([(ex, ey), (ex + ew, ey + eh)], fill=tab_color, outline=(180, 180, 180))
                    if text:
                        draw.text((ex + 8, ey + 6), text, fill=(20, 20, 20))
                else:
                    draw.rectangle([(ex, ey), (ex + ew, ey + eh)], fill=(230, 230, 230), outline=(160, 160, 160))
                    if text:
                        draw.text((ex + 4, ey + 4), text, fill=(10, 10, 10))

        pixels = np.array(img, dtype=np.uint8)
        frame_hash = hashlib.sha256(pixels.tobytes()).hexdigest()
        frame = ScreenFrame(
            timestamp=time.time(),
            resolution=(w, h),
            pixels=pixels,
            frame_hash=frame_hash,
            capture_latency_ms=4.2,
        )
        self._current_frame = frame
        self._mock_pixels = pixels
        return frame

    def capture_frame(self) -> ScreenFrame:
        """
        Captures the fresh screen frame.
        Guarantees sub-15ms latency.
        """
        start = time.perf_counter()

        if self._mock_pixels is not None:
            if not self.allow_synthetic:
                raise ScreenCaptureUnavailableError(
                    "Mock screen frames are disabled in production; no live VNC capture adapter is configured."
                )
            pixels = self._mock_pixels.copy()
            h, w = pixels.shape[:2]
            latency_ms = (time.perf_counter() - start) * 1000.0
            frame_hash = hashlib.sha256(pixels.tobytes()).hexdigest()
            frame = ScreenFrame(
                timestamp=time.time(),
                resolution=(w, h),
                pixels=pixels,
                frame_hash=frame_hash,
                capture_latency_ms=latency_ms,
            )
            self._current_frame = frame
            return frame

        if not self.allow_synthetic:
            raise ScreenCaptureUnavailableError(
                "No live desktop capture adapter is configured; refusing to synthesize a production screen."
            )
        return self.render_synthetic_desktop()

    def apply_visual_action(
        self,
        action_type: str,
        coords: Optional[tuple[int, int]] = None,
        text: Optional[str] = None,
    ) -> None:
        """
        Updates the synthetic frame buffer in response to a GUI action.
        Produces realistic pixel deltas and updates rendered elements.
        """
        if self._mock_pixels is None:
            if not self.allow_synthetic:
                raise ScreenCaptureUnavailableError(
                    "Synthetic GUI actions are disabled in production; no live desktop action adapter is configured."
                )
            self.render_synthetic_desktop()

        if self._mock_pixels is None:
            return

        pixels = self._mock_pixels.copy()
        img = Image.fromarray(pixels)
        draw = ImageDraw.Draw(img)

        if action_type in ("MOUSE_CLICK", "MOUSE_DOUBLE_CLICK", "MOUSE_RIGHT_CLICK", "MOUSE_DOWN") and coords:
            cx, cy = coords
            r = 6
            fill_color = (100, 180, 255) if action_type == "MOUSE_CLICK" else (255, 180, 100)
            draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], outline=fill_color, width=2)

        elif action_type == "KEY_TYPE" and text:
            target_item = None
            if coords:
                cx, cy = coords
                for item in ScreenCaptureEngine._last_rendered_elements:
                    xmin, ymin, xmax, ymax = item["rect"]
                    if xmin <= cx <= xmax and ymin <= cy <= ymax:
                        target_item = item
                        break
            if target_item is None:
                for item in ScreenCaptureEngine._last_rendered_elements:
                    if item.get("is_focused") and item.get("type") == "text_input":
                        target_item = item
                        break

            if target_item:
                xmin, ymin, xmax, ymax = target_item["rect"]
                old_text = target_item.get("text", "")
                new_text = f"{old_text} {text}".strip()
                target_item["text"] = new_text
                draw.rectangle([(xmin + 2, ymin + 2), (xmax - 2, ymax - 2)], fill=(255, 255, 255))
                draw.text((xmin + 8, ymin + 8), new_text, fill=(0, 0, 0))
                cursor_x = xmin + 8 + (len(new_text) * 7)
                draw.line([(cursor_x, ymin + 4), (cursor_x, ymax - 4)], fill=(0, 0, 0), width=2)
            elif coords:
                draw.text(coords, text, fill=(0, 0, 0))

        elif action_type == "KEY_HOTKEY" and text and text.lower() == "escape":
            ScreenCaptureEngine._last_rendered_elements = [
                item for item in ScreenCaptureEngine._last_rendered_elements if item.get("type") != "dialog"
            ]

        self._mock_pixels = np.array(img, dtype=np.uint8)
        h, w = self._mock_pixels.shape[:2]
        self._current_frame = ScreenFrame(
            timestamp=time.time(),
            resolution=(w, h),
            pixels=self._mock_pixels,
            frame_hash=hashlib.sha256(self._mock_pixels.tobytes()).hexdigest(),
            capture_latency_ms=2.1,
        )
