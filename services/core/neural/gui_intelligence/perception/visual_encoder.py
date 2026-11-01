from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image

from neural.gui_intelligence.models import ScreenFrame
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.perception.visual_encoder")


@dataclass
class PyramidalSample:
    global_view: np.ndarray  # (H_coarse, W_coarse, 3)
    foveal_patch: np.ndarray  # (H_fine, W_fine, 3)
    focus_point: tuple[int, int]
    global_resolution: tuple[int, int]
    foveal_bounds: tuple[int, int, int, int]  # (xmin, ymin, xmax, ymax) in screen coords
    foveal_signature: str
    encoding_latency_ms: float


class PyramidalVisualEncoder:
    """
    Dual-Fovea Pyramidal Visual Encoder (Section 5.1).
    Transforms full raw screenshots into a dual-resolution representation:
    - Global Peripheral Stream: downscaled to 768x432 in < 8ms.
    - High-Resolution Foveal Stream: 384x384 uncompressed crop around focus coordinates.
    Computes perceptual difference hash (dHash) for visual element signature matching.
    """

    def __init__(
        self,
        coarse_resolution: tuple[int, int] = (768, 432),
        foveal_size: tuple[int, int] = (384, 384),
    ) -> None:
        self.coarse_resolution = coarse_resolution
        self.foveal_size = foveal_size

    @staticmethod
    def compute_dhash(patch: np.ndarray, hash_size: int = 8) -> str:
        """
        Computes 64-bit perceptual difference hash (dHash) of a patch.
        Fast, rotation/intensity-tolerant visual fingerprint using vectorised grid sampling.
        """
        h, w = patch.shape[:2]
        if h < hash_size or w < hash_size + 1:
            return "0000000000000000"
        ys = np.linspace(0, h - 1, hash_size, dtype=int)
        xs = np.linspace(0, w - 1, hash_size + 1, dtype=int)
        grid = patch[np.ix_(ys, xs)]
        gray = 0.299 * grid[:, :, 0] + 0.587 * grid[:, :, 1] + 0.114 * grid[:, :, 2]
        diff = gray[:, 1:] > gray[:, :-1]
        bits = "".join("1" if b else "0" for b in diff.flatten())
        return hex(int(bits, 2))[2:].zfill(16)

    def encode(
        self,
        frame: ScreenFrame,
        focus_point: Optional[tuple[int, int]] = None,
    ) -> PyramidalSample:
        """
        Generates the dual-fovea pyramidal representation from a screen frame.
        """
        start = time.perf_counter()
        w, h = frame.resolution

        # 1. Global Peripheral Stream: Downscale full frame using area/box SIMD filtering
        pil_frame = Image.fromarray(frame.pixels)
        coarse_img = pil_frame.resize(self.coarse_resolution, Image.Resampling.BOX)
        global_view = np.array(coarse_img, dtype=np.uint8)

        # 2. Determine focus point (defaults to screen center if not provided)
        if focus_point is None:
            focus_x, focus_y = w // 2, h // 2
        else:
            focus_x = max(0, min(w - 1, focus_point[0]))
            focus_y = max(0, min(h - 1, focus_point[1]))

        # 3. High-Resolution Foveal Stream: Crop 384x384 uncompressed region around focus
        fw, fh = self.foveal_size
        half_w, half_h = fw // 2, fh // 2

        # Clamp crop bounds to keep foveal box inside screen
        xmin = max(0, min(w - fw, focus_x - half_w))
        ymin = max(0, min(h - fh, focus_y - half_h))
        xmax = min(w, xmin + fw)
        ymax = min(h, ymin + fh)

        foveal_crop = frame.pixels[ymin:ymax, xmin:xmax, :].copy()

        # If screen is smaller than foveal size, pad crop
        if foveal_crop.shape[0] < fh or foveal_crop.shape[1] < fw:
            padded = np.zeros((fh, fw, 3), dtype=np.uint8)
            padded[: foveal_crop.shape[0], : foveal_crop.shape[1], :] = foveal_crop
            foveal_crop = padded

        foveal_sig = self.compute_dhash(foveal_crop)
        latency_ms = (time.perf_counter() - start) * 1000.0

        return PyramidalSample(
            global_view=global_view,
            foveal_patch=foveal_crop,
            focus_point=(focus_x, focus_y),
            global_resolution=self.coarse_resolution,
            foveal_bounds=(xmin, ymin, xmax, ymax),
            foveal_signature=foveal_sig,
            encoding_latency_ms=latency_ms,
        )

