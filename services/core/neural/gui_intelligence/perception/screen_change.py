from __future__ import annotations

from typing import Optional

import numpy as np

from neural.gui_intelligence.models import ScreenFrame, VisualDelta
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.perception.screen_change")


class ScreenChangeDetector:
    """
    Optical Flow & Screen Delta Recognition (Section 5.3).
    Computes temporal pixel deltas between consecutive frames using coarse grid block hashing (16x16 blocks).
    Detects UI stabilization / settling: declared settled when pixel change magnitude < 0.05% (0.0005).
    """

    SETTLE_THRESHOLD: float = 0.0005  # < 0.05% pixel change

    def __init__(self, block_size: int = 16) -> None:
        self.block_size = block_size

    def compute_delta(
        self,
        frame_before: ScreenFrame,
        frame_after: ScreenFrame,
    ) -> VisualDelta:
        """
        Computes the pixel delta between two frames.
        Returns visual change magnitude in [0.0, 1.0] and block change statistics.
        """
        if frame_before.resolution != frame_after.resolution:
            # Different resolutions mean a major screen/window mode change
            return VisualDelta(
                magnitude=1.0,
                changed_blocks=1,
                total_blocks=1,
                heatmap=None,
                is_settled=False,
            )

        p1 = frame_before.pixels.astype(np.int16)
        p2 = frame_after.pixels.astype(np.int16)

        # Absolute difference across RGB channels
        diff = np.abs(p2 - p1)
        # Pixel is considered changed if sum of RGB differences exceeds threshold
        pixel_changed = np.sum(diff, axis=2) > 15

        total_pixels = pixel_changed.size
        changed_pixels = int(np.sum(pixel_changed))
        magnitude = changed_pixels / total_pixels if total_pixels > 0 else 0.0

        # Coarse 16x16 block analysis
        h, w = frame_before.pixels.shape[:2]
        bs = self.block_size
        n_blocks_y = (h + bs - 1) // bs
        n_blocks_x = (w + bs - 1) // bs
        total_blocks = n_blocks_y * n_blocks_x

        # Create block heatmap
        heatmap = np.zeros((n_blocks_y, n_blocks_x), dtype=np.float32)
        changed_blocks = 0

        for by in range(n_blocks_y):
            y_start = by * bs
            y_end = min(h, y_start + bs)
            for bx in range(n_blocks_x):
                x_start = bx * bs
                x_end = min(w, x_start + bs)

                block_changed = np.sum(pixel_changed[y_start:y_end, x_start:x_end])
                block_total = (y_end - y_start) * (x_end - x_start)
                if block_total > 0:
                    block_ratio = block_changed / block_total
                    heatmap[by, bx] = block_ratio
                    if block_ratio > 0.02:  # block has significant change
                        changed_blocks += 1

        is_settled = magnitude < self.SETTLE_THRESHOLD

        return VisualDelta(
            magnitude=float(magnitude),
            changed_blocks=changed_blocks,
            total_blocks=total_blocks,
            heatmap=heatmap,
            is_settled=is_settled,
        )

    def is_settled(
        self,
        frame_history: list[ScreenFrame],
        threshold: Optional[float] = None,
    ) -> bool:
        """
        Verifies if UI has stabilized over consecutive frames in frame_history.
        Requires at least 2 frames.
        """
        settle_thresh = threshold or self.SETTLE_THRESHOLD
        if len(frame_history) < 2:
            return True

        delta = self.compute_delta(frame_history[-2], frame_history[-1])
        return delta.magnitude < settle_thresh
