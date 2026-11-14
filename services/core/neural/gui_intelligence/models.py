from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import numpy as np
from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Section 19: Typed API Contracts
# ---------------------------------------------------------------------------

class GUIActionRequest(BaseModel):
    action_id: str
    employee_id: str
    action_type: Literal[
        "MOUSE_MOVE",
        "MOUSE_CLICK",
        "MOUSE_DOUBLE_CLICK",
        "MOUSE_RIGHT_CLICK",
        "MOUSE_DRAG",
        "MOUSE_SCROLL",
        "KEY_PRESS",
        "KEY_TYPE",
        "KEY_HOTKEY",
        "WAIT_SETTLE",
    ]
    coordinates: Optional[tuple[int, int]] = Field(
        None, description="Explicit screen pixel coordinates (x, y)"
    )
    drag_end_coordinates: Optional[tuple[int, int]] = Field(
        None, description="Drag destination pixel coordinates (x, y)"
    )
    text_content: Optional[str] = Field(
        None, description="String to type into focused element"
    )
    key_code: Optional[str] = Field(
        None, description="Single key or hotkey specification (e.g. 'Return', 'ctrl+c')"
    )
    scroll_delta: Optional[int] = Field(
        None, description="Vertical scroll delta (positive=down, negative=up)"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    action_chunk_index: int = 0
    total_chunk_size: int = 1
    target_element_id: Optional[str] = None
    # Type into the window that already holds OS keyboard focus (e.g. a GTK file
    # dialog's location bar) without clicking a grounded coordinate first.
    active_focus: bool = False
    # Optional WAIT_SETTLE duration in milliseconds; defaults to the loop's fixed wait.
    settle_ms: Optional[int] = None


class GUIActionReceipt(BaseModel):
    action_id: str
    employee_id: str
    status: Literal[
        "SUCCESS",
        "BLOCKED_BY_SAFETY",
        "UNCERTAINTY_ESCALATION",
        "TIMEOUT",
        "FOCUS_FAILED",
        "RECOVERY_TRIGGERED",
    ]
    execution_time_ms: float
    screen_settled: bool
    pixel_delta_magnitude: float
    observed_postcondition: Optional[str]
    error_message: Optional[str] = None
    signature: str


class ExperienceTrajectoryRecord(BaseModel):
    trajectory_id: str
    task_id: str
    employee_id: str
    application_name: str
    application_version: str
    screen_resolution: tuple[int, int]
    start_time: str
    end_time: str
    total_actions: int
    actions: list[dict[str, Any]]
    screenshot_hashes: list[str]
    verification_passed: bool
    verified_speedup_ratio: float


# ---------------------------------------------------------------------------
# Section 6: Visual Elements & Belief State
# ---------------------------------------------------------------------------

@dataclass
class VisualElement:
    element_id: str
    bounding_box: tuple[int, int, int, int]  # x_min, y_min, x_max, y_max (screen pixels)
    element_type: str  # button, text_input, icon, tab, menu, checkbox, scrollbar, window_title, dropdown, dialog
    visual_text: Optional[str]
    visual_signature: str  # perceptual dHash / SHA hash of pixel patch
    confidence: float
    is_interactive: bool
    is_focused: bool = False

    def center(self) -> tuple[int, int]:
        xmin, ymin, xmax, ymax = self.bounding_box
        return ((xmin + xmax) // 2, (ymin + ymax) // 2)

    def contains_point(self, x: int, y: int) -> bool:
        xmin, ymin, xmax, ymax = self.bounding_box
        return xmin <= x <= xmax and ymin <= y <= ymax

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VisualWindowState:
    window_id: str
    bounding_box: tuple[int, int, int, int]
    title_text: str
    is_active: bool
    is_maximized: bool
    detected_elements: list[VisualElement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "bounding_box": self.bounding_box,
            "title_text": self.title_text,
            "is_active": self.is_active,
            "is_maximized": self.is_maximized,
            "detected_elements": [e.to_dict() for e in self.detected_elements],
        }


@dataclass
class DesktopBeliefState:
    timestamp: float
    screen_resolution: tuple[int, int]
    active_window: Optional[VisualWindowState]
    visible_windows: list[VisualWindowState]
    active_dialogs: list[VisualElement]
    cursor_position: tuple[int, int]
    last_action_dispatched: Optional[dict[str, Any]]
    predicted_transition: Optional[str]
    uncertainty_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "screen_resolution": self.screen_resolution,
            "active_window": self.active_window.to_dict() if self.active_window else None,
            "visible_windows": [w.to_dict() for w in self.visible_windows],
            "active_dialogs": [d.to_dict() for d in self.active_dialogs],
            "cursor_position": self.cursor_position,
            "last_action_dispatched": self.last_action_dispatched,
            "predicted_transition": self.predicted_transition,
            "uncertainty_score": self.uncertainty_score,
        }


@dataclass
class ScreenFrame:
    timestamp: float
    resolution: tuple[int, int]  # (width, height)
    pixels: np.ndarray  # shape (H, W, 3), uint8
    frame_hash: str
    capture_latency_ms: float = 0.0

    @property
    def width(self) -> int:
        return self.resolution[0]

    @property
    def height(self) -> int:
        return self.resolution[1]


@dataclass
class VisualDelta:
    magnitude: float
    changed_blocks: int
    total_blocks: int
    heatmap: Optional[np.ndarray] = None
    is_settled: bool = True

