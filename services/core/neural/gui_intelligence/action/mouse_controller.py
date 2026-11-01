from __future__ import annotations

import math
import time
from typing import Callable, Optional

from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.action.mouse_controller")


class MouseController:
    """
    Cosine-Eased Smooth Motor Movement & Mouse Controller (Section 9.1).
    Prohibits 0ms instantaneous cursor teleportation (Strict Prohibitions 10 & 11).
    Interpolates cursor positions using smooth cosine easing over T_move in [180ms, 280ms]:
      x(t) = x0 + (x1 - x0) * (1 - cos(pi * t / T_move)) / 2
      y(t) = y0 + (y1 - y0) * (1 - cos(pi * t / T_move)) / 2
    """

    DEFAULT_MOVE_DURATION_MS: float = 220.0  # within [180ms, 280ms]
    NUM_WAYPOINTS: int = 10

    def __init__(
        self,
        initial_position: tuple[int, int] = (640, 400),
        screen_resolution: tuple[int, int] = (1280, 800),
        dispatch_fn: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self.cursor_position: tuple[int, int] = initial_position
        self.screen_resolution = screen_resolution
        self.dispatch_fn = dispatch_fn
        self.action_history: list[dict] = []

    def compute_trajectory_waypoints(
        self,
        start_pos: tuple[int, int],
        target_pos: tuple[int, int],
        duration_ms: Optional[float] = None,
        num_waypoints: Optional[int] = None,
    ) -> list[tuple[int, int, float]]:
        """
        Calculates cosine-eased trajectory waypoints (x, y, timestamp_ms).
        """
        dur = duration_ms or self.DEFAULT_MOVE_DURATION_MS
        steps = num_waypoints or self.NUM_WAYPOINTS
        x0, y0 = start_pos
        x1, y1 = target_pos

        waypoints: list[tuple[int, int, float]] = []
        for step in range(steps + 1):
            ratio = step / float(steps)
            ease = (1.0 - math.cos(math.pi * ratio)) / 2.0
            x = int(round(x0 + (x1 - x0) * ease))
            y = int(round(y0 + (y1 - y0) * ease))
            t_ms = ratio * dur
            waypoints.append((x, y, t_ms))

        return waypoints

    def move_to(
        self,
        x: int,
        y: int,
        duration_ms: Optional[float] = None,
    ) -> list[tuple[int, int, float]]:
        """
        Executes smooth cosine-eased mouse movement to target coordinates (x, y).
        """
        start_time = time.perf_counter()
        # Clamp to screen bounds
        w, h = self.screen_resolution
        clamped_x = max(0, min(w - 1, x))
        clamped_y = max(0, min(h - 1, y))

        waypoints = self.compute_trajectory_waypoints(
            self.cursor_position,
            (clamped_x, clamped_y),
            duration_ms=duration_ms,
        )

        for wx, wy, _ in waypoints:
            self.cursor_position = (wx, wy)
            if self.dispatch_fn:
                self.dispatch_fn("MOUSE_MOVE", {"x": wx, "y": wy})

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        self.action_history.append(
            {
                "type": "MOUSE_MOVE",
                "target": (clamped_x, clamped_y),
                "waypoints": len(waypoints),
                "duration_ms": elapsed_ms,
            }
        )
        return waypoints

    def click(self, x: int, y: int) -> float:
        """Moves cursor to (x, y) smoothly and performs left click."""
        start = time.perf_counter()
        self.move_to(x, y)
        if self.dispatch_fn:
            self.dispatch_fn("MOUSE_CLICK", {"x": x, "y": y, "button": 1})
        elapsed = (time.perf_counter() - start) * 1000.0
        self.action_history.append({"type": "MOUSE_CLICK", "coords": (x, y), "elapsed_ms": elapsed})
        return elapsed

    def double_click(self, x: int, y: int) -> float:
        """Moves cursor to (x, y) smoothly and performs double click."""
        start = time.perf_counter()
        self.move_to(x, y)
        if self.dispatch_fn:
            self.dispatch_fn("MOUSE_DOUBLE_CLICK", {"x": x, "y": y, "button": 1})
        elapsed = (time.perf_counter() - start) * 1000.0
        self.action_history.append({"type": "MOUSE_DOUBLE_CLICK", "coords": (x, y), "elapsed_ms": elapsed})
        return elapsed

    def right_click(self, x: int, y: int) -> float:
        """Moves cursor to (x, y) smoothly and performs right click."""
        start = time.perf_counter()
        self.move_to(x, y)
        if self.dispatch_fn:
            self.dispatch_fn("MOUSE_RIGHT_CLICK", {"x": x, "y": y, "button": 3})
        elapsed = (time.perf_counter() - start) * 1000.0
        self.action_history.append({"type": "MOUSE_RIGHT_CLICK", "coords": (x, y), "elapsed_ms": elapsed})
        return elapsed

    def drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> float:
        """Smoothly drags from (start_x, start_y) to (end_x, end_y)."""
        start = time.perf_counter()
        self.move_to(start_x, start_y)
        if self.dispatch_fn:
            self.dispatch_fn("MOUSE_DOWN", {"x": start_x, "y": start_y, "button": 1})
        self.move_to(end_x, end_y, duration_ms=250.0)
        if self.dispatch_fn:
            self.dispatch_fn("MOUSE_UP", {"x": end_x, "y": end_y, "button": 1})
        elapsed = (time.perf_counter() - start) * 1000.0
        self.action_history.append({"type": "MOUSE_DRAG", "from": (start_x, start_y), "to": (end_x, end_y), "elapsed_ms": elapsed})
        return elapsed

    def scroll(self, delta: int, x: Optional[int] = None, y: Optional[int] = None) -> float:
        """Scrolls mouse wheel by delta (positive=down, negative=up)."""
        start = time.perf_counter()
        if x is not None and y is not None:
            self.move_to(x, y)
        if self.dispatch_fn:
            self.dispatch_fn("MOUSE_SCROLL", {"delta": delta, "x": self.cursor_position[0], "y": self.cursor_position[1]})
        elapsed = (time.perf_counter() - start) * 1000.0
        self.action_history.append({"type": "MOUSE_SCROLL", "delta": delta, "elapsed_ms": elapsed})
        return elapsed

