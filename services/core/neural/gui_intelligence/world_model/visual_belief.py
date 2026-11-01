from __future__ import annotations

import time
from typing import Any, Optional

from neural.gui_intelligence.models import (
    DesktopBeliefState,
    ScreenFrame,
    VisualElement,
    VisualWindowState,
)
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.world_model.visual_belief")


class VisualBeliefStateManager:
    """
    Visual Belief State & Uncertainty Metric (Section 6.1 & 6.2).
    Maintains the employee's internal cognitive belief of the desktop environment.
    Computes real-time spatial and state uncertainty scores.
    Signals escalation when uncertainty > 0.12.
    """

    UNCERTAINTY_ESCALATION_THRESHOLD: float = 0.12

    def __init__(self, screen_resolution: tuple[int, int] = (1280, 800)) -> None:
        self.screen_resolution = screen_resolution
        self.cursor_position: tuple[int, int] = (screen_resolution[0] // 2, screen_resolution[1] // 2)
        self.active_window: Optional[VisualWindowState] = None
        self.visible_windows: list[VisualWindowState] = []
        self.active_dialogs: list[VisualElement] = []
        self.last_action_dispatched: Optional[dict[str, Any]] = None
        self.predicted_transition: Optional[str] = None
        self.uncertainty_score: float = 0.05
        self._recent_frames: list[ScreenFrame] = []

    def get_state(self) -> DesktopBeliefState:
        """Returns the current immutable belief state snapshot."""
        return DesktopBeliefState(
            timestamp=time.time(),
            screen_resolution=self.screen_resolution,
            active_window=self.active_window,
            visible_windows=list(self.visible_windows),
            active_dialogs=list(self.active_dialogs),
            cursor_position=self.cursor_position,
            last_action_dispatched=self.last_action_dispatched,
            predicted_transition=self.predicted_transition,
            uncertainty_score=self.uncertainty_score,
        )

    def update_belief(
        self,
        frame: ScreenFrame,
        visible_windows: list[VisualWindowState],
        active_dialogs: list[VisualElement],
        cursor_position: Optional[tuple[int, int]] = None,
        last_action: Optional[dict[str, Any]] = None,
        grounding_uncertainty: float = 0.0,
        transition_anomaly: bool = False,
    ) -> DesktopBeliefState:
        """
        Updates belief state based on newly perceived frame, detected windows, and action feedback.
        Calculates compounded uncertainty score.
        """
        self.screen_resolution = frame.resolution
        self._recent_frames.append(frame)
        if len(self._recent_frames) > 5:
            self._recent_frames.pop(0)

        if cursor_position is not None:
            self.cursor_position = cursor_position

        self.visible_windows = list(visible_windows)
        self.active_window = visible_windows[0] if visible_windows else None
        self.active_dialogs = list(active_dialogs)

        if last_action is not None:
            self.last_action_dispatched = last_action

        # Compound uncertainty calculation
        score = max(0.02, grounding_uncertainty)

        # Unexpected modal dialog appearance adds substantial uncertainty
        if self.active_dialogs:
            score = max(score, 0.45)

        # Transition anomaly adds uncertainty
        if transition_anomaly:
            score = max(score, 0.85)

        self.uncertainty_score = float(min(1.0, score))
        return self.get_state()

    def set_cursor_position(self, x: int, y: int) -> None:
        self.cursor_position = (x, y)

    def set_predicted_transition(self, expectation: Optional[str]) -> None:
        self.predicted_transition = expectation

    @property
    def requires_escalation(self) -> bool:
        """True if visual uncertainty exceeds the confidence escort threshold."""
        return self.uncertainty_score > self.UNCERTAINTY_ESCALATION_THRESHOLD

