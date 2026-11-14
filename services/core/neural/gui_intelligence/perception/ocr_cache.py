"""Bounded, configuration-aware cache for region OCR results.

The strict GUI contract reads text from raster pixels, and tesseract on a full
1280x800 desktop is the dominant per-step cost (~1 s at the engine's real 2x/3x
upscales). A whole-frame hash cache is a poor fit for a live desktop: the cursor
blinks, the clock ticks, and a caret moves, so the full-frame hash changes on
every capture even when the region that matters is byte-identical. This cache
keys on the *region's own pixels* plus the geometry and OCR configuration, so an
unchanged region is reused while a change elsewhere on screen cannot poison it.

Correctness rules encoded here:

* The key includes the region offset, so identical pixels at a different screen
  position are a different entry and never return wrongly-positioned boxes.
* The key includes the screen dimensions, the OCR engine identity, the language,
  the PSM, the upscale set and a normalization version, so results never survive
  an incompatible OCR configuration change.
* Entries are bounded by an LRU with a configurable maximum, so a long run cannot
  grow memory without limit.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

from neural.gui_intelligence.perception.pixel_ocr import OCRResult

# Bump when the OCR post-processing (parsing, normalization, box mapping) changes
# in a way that would make previously cached results wrong for the same pixels.
OCR_NORMALIZATION_VERSION = 1

DEFAULT_MAX_ENTRIES = 64


@dataclass(frozen=True)
class OCRRegion:
    """An axis-aligned region of the screen, in original screen coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    def clamped(self, screen_width: int, screen_height: int) -> Optional["OCRRegion"]:
        """Clip to the screen; return None when the region is empty or off-screen."""
        x0 = max(0, min(int(self.x), screen_width))
        y0 = max(0, min(int(self.y), screen_height))
        x1 = max(0, min(int(self.x) + int(self.width), screen_width))
        y1 = max(0, min(int(self.y) + int(self.height), screen_height))
        if x1 - x0 < 1 or y1 - y0 < 1:
            return None
        return OCRRegion(x0, y0, x1 - x0, y1 - y0)

    @staticmethod
    def from_bounding_box(
        bounding_box: tuple[int, int, int, int],
        margin: int,
        screen_width: int,
        screen_height: int,
    ) -> Optional["OCRRegion"]:
        """Build a context-expanded region around a bounding box, clamped to screen."""
        xmin, ymin, xmax, ymax = bounding_box
        region = OCRRegion(
            int(xmin) - margin,
            int(ymin) - margin,
            (int(xmax) - int(xmin)) + 2 * margin,
            (int(ymax) - int(ymin)) + 2 * margin,
        )
        return region.clamped(screen_width, screen_height)


@dataclass
class OCRCacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    stores: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "stores": self.stores,
        }

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class RegionOCRCache:
    """LRU cache of OCR results keyed on region pixels + geometry + OCR config."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self.max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[tuple, list[OCRResult]]" = OrderedDict()
        self.stats = OCRCacheStats()
        self._screen: Optional[tuple[int, int]] = None

    # -- key -------------------------------------------------------------------

    @staticmethod
    def region_pixel_hash(pixels: np.ndarray) -> str:
        return hashlib.sha256(np.ascontiguousarray(pixels).tobytes()).hexdigest()

    def make_key(
        self,
        region: OCRRegion,
        pixels: np.ndarray,
        screen: tuple[int, int],
        config_signature: str,
    ) -> tuple:
        # Offset is part of the key so identical pixels elsewhere are a distinct
        # entry: a cached box is only valid for the position it was read at.
        return (
            region.to_tuple(),
            self.region_pixel_hash(pixels),
            (int(screen[0]), int(screen[1])),
            config_signature,
        )

    # -- lookup / store --------------------------------------------------------

    def get(self, key: tuple) -> Optional[list[OCRResult]]:
        entry = self._entries.get(key)
        if entry is None:
            self.stats.misses += 1
            return None
        self._entries.move_to_end(key)
        self.stats.hits += 1
        # Return copies: callers must not be able to mutate cached boxes.
        return [OCRResult(r.text, r.bounding_box, r.confidence) for r in entry]

    def put(self, key: tuple, results: Iterable[OCRResult]) -> None:
        self._entries[key] = [OCRResult(r.text, r.bounding_box, r.confidence) for r in results]
        self._entries.move_to_end(key)
        self.stats.stores += 1
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self.stats.evictions += 1

    # -- invalidation ----------------------------------------------------------

    def invalidate(self) -> None:
        self._entries.clear()

    def note_screen(self, screen: tuple[int, int]) -> None:
        """Drop everything when the screen resolution changes.

        The screen size is already in the key, so this is belt-and-braces: a
        resolution change must not leave stale geometry reachable.
        """
        screen = (int(screen[0]), int(screen[1]))
        if self._screen is not None and screen != self._screen:
            self.invalidate()
        self._screen = screen

    def __len__(self) -> int:
        return len(self._entries)
