from __future__ import annotations

import math
from typing import Optional

from neural.gui_intelligence.models import ScreenFrame, VisualElement
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.world_model.screen_state")


class ScreenStateManager:
    """
    Screen State Manager (Section 6.1).
    Maintains spatial visible elements and layout on the screen.
    Enables spatial queries without querying OS accessibility APIs.
    """

    def __init__(self) -> None:
        self._elements: list[VisualElement] = []
        self._resolution: tuple[int, int] = (1280, 800)

    @property
    def elements(self) -> list[VisualElement]:
        return list(self._elements)

    def update_state(self, frame: ScreenFrame, elements: list[VisualElement]) -> None:
        """Updates internal spatial representation with fresh frame elements."""
        self._resolution = frame.resolution
        self._elements = list(elements)

    def element_at(self, x: int, y: int) -> Optional[VisualElement]:
        """Finds topmost interactive element containing pixel coordinate (x, y)."""
        # Search in reverse (top-most elements first)
        for elem in reversed(self._elements):
            if elem.contains_point(x, y):
                return elem
        return None

    def find_by_type(self, element_type: str) -> list[VisualElement]:
        """Returns all visible elements matching the specified type."""
        norm_type = element_type.strip().lower()
        return [e for e in self._elements if e.element_type.lower() == norm_type]

    def find_by_text(self, text_query: str, case_sensitive: bool = False) -> list[VisualElement]:
        """Returns all elements whose visual text contains text_query."""
        q = text_query if case_sensitive else text_query.lower()
        results: list[VisualElement] = []
        for elem in self._elements:
            t = elem.visual_text or ""
            t_cmp = t if case_sensitive else t.lower()
            if q in t_cmp:
                results.append(elem)
        return results

    def find_closest(
        self,
        x: int,
        y: int,
        element_type: Optional[str] = None,
    ) -> Optional[VisualElement]:
        """Finds the closest element to (x, y), optionally filtered by type."""
        candidates = self._elements
        if element_type:
            norm_type = element_type.strip().lower()
            candidates = [e for e in candidates if e.element_type.lower() == norm_type]

        if not candidates:
            return None

        def distance(elem: VisualElement) -> float:
            cx, cy = elem.center()
            return math.hypot(cx - x, cy - y)

        return min(candidates, key=distance)

