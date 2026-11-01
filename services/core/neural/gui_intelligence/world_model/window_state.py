from __future__ import annotations

from typing import Optional

from neural.gui_intelligence.models import ScreenFrame, VisualElement, VisualWindowState
from neural.gui_intelligence.perception.pixel_ocr import PixelOCREngine
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.world_model.window_state")


class WindowStateTracker:
    """
    Window State Tracker (Section 6.1).
    Tracks detected windows, active window focus, z-order stacking, and modal dialog overlays.
    Reads window titles visually from raster pixels without OS window manager APIs.
    """

    def __init__(self, ocr_engine: Optional[PixelOCREngine] = None) -> None:
        self.ocr_engine = ocr_engine or PixelOCREngine()
        self._windows: list[VisualWindowState] = []  # Ordered from bottom to top (z-order)

    @property
    def visible_windows(self) -> list[VisualWindowState]:
        """Returns all visible windows, ordered from topmost (front) to bottommost (back)."""
        return list(reversed(self._windows))

    @property
    def active_window(self) -> Optional[VisualWindowState]:
        """Returns the currently active / focused window (topmost active)."""
        for win in reversed(self._windows):
            if win.is_active:
                return win
        return self._windows[-1] if self._windows else None

    def detect_active_dialogs(self) -> list[VisualElement]:
        """Returns any active modal dialog elements that may overlay the desktop."""
        dialogs: list[VisualElement] = []
        for win in reversed(self._windows):
            for elem in win.detected_elements:
                if elem.element_type == "dialog":
                    dialogs.append(elem)
        return dialogs

    def register_window(
        self,
        window_id: str,
        bounding_box: tuple[int, int, int, int],
        title_text: Optional[str] = None,
        is_active: bool = True,
        is_maximized: bool = False,
        elements: Optional[list[VisualElement]] = None,
        frame: Optional[ScreenFrame] = None,
    ) -> VisualWindowState:
        """Registers or updates a window in the visual z-order stack."""
        if title_text is None and frame is not None:
            title_text = self.ocr_engine.read_title_bar(frame, bounding_box)
        elif title_text is None:
            title_text = f"Window {window_id}"

        elements = elements or []

        # If this window is set to active, deactivate previous windows
        if is_active:
            for w in self._windows:
                w.is_active = False

        # Remove existing window with same ID if present
        self._windows = [w for w in self._windows if w.window_id != window_id]

        win_state = VisualWindowState(
            window_id=window_id,
            bounding_box=bounding_box,
            title_text=title_text,
            is_active=is_active,
            is_maximized=is_maximized,
            detected_elements=elements,
        )
        self._windows.append(win_state)
        return win_state

    def bring_to_front(self, window_id: str) -> bool:
        """Brings the specified window to top of z-order and sets it as active."""
        target = None
        for w in self._windows:
            if w.window_id == window_id:
                target = w
                break

        if not target:
            return False

        for w in self._windows:
            w.is_active = False

        self._windows.remove(target)
        target.is_active = True
        self._windows.append(target)
        return True

    def update_from_elements(self, frame: ScreenFrame, elements: list[VisualElement]) -> list[VisualWindowState]:
        """
        Groups detected elements into windows and modal dialogs based on spatial containment.
        """
        w, h = frame.resolution

        # Check for dialog elements
        dialog_elements = [e for e in elements if e.element_type == "dialog"]
        for d in dialog_elements:
            self.register_window(
                window_id=f"dialog_{d.element_id}",
                bounding_box=d.bounding_box,
                title_text=d.visual_text or "Modal Dialog",
                is_active=True,
                is_maximized=False,
                elements=[d],
            )

        # If no windows are currently registered, create a default desktop workspace window
        if not self._windows:
            self.register_window(
                window_id="main_desktop_win",
                bounding_box=(0, 0, w, h),
                title_text="ANCOS Desktop",
                is_active=True,
                is_maximized=True,
                elements=elements,
            )
        else:
            # Assign elements to topmost window that contains them
            for elem in elements:
                cx, cy = elem.center()
                for win in reversed(self._windows):
                    xmin, ymin, xmax, ymax = win.bounding_box
                    if xmin <= cx <= xmax and ymin <= cy <= ymax:
                        if elem not in win.detected_elements:
                            win.detected_elements.append(elem)
                        break

        return self.visible_windows

