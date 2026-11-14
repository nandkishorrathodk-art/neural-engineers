"""Stage 2 tests: intelligent OCR optimization and visual grounding performance.

These lock in the behavior that reduces and eliminates redundant OCR work without
changing the strict GUI-only execution contract:

* a bounded region cache keyed on the *region's own pixels* (not the whole-frame
  hash) survives an unrelated change elsewhere on screen;
* the cache key includes geometry and OCR configuration, so a stale entry can
  never be returned for a different position or a different OCR config;
* region OCR returns boxes in original screen coordinates and matches the
  full-frame result for the same target;
* a confident region hint accelerates grounding and resolves the *same*
  coordinates as a full-frame read, while a wrong/stale hint falls back to the
  full frame instead of producing a wrong click.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest
from neural.gui_intelligence.models import ScreenFrame
from neural.gui_intelligence.perception.ocr_cache import OCRRegion, RegionOCRCache
from neural.gui_intelligence.perception.ocr_runtime import TesseractOCREngine
from neural.gui_intelligence.perception.pixel_ocr import OCRResult
from neural.gui_intelligence.perception.visual_grounding import VisualGrounder


def _frame(pixels: np.ndarray, tag: str = "f") -> ScreenFrame:
    h, w = pixels.shape[:2]
    return ScreenFrame(timestamp=0.0, resolution=(w, h), pixels=pixels, frame_hash=tag)


def _stub_engine(tmp_path, *, text: str, conf: int = 90) -> TesseractOCREngine:
    """Build an engine whose tesseract binary emits one fixed word box."""
    stub = tmp_path / "tesseract"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "print('level\\tpage_num\\tblock_num\\tpar_num\\tline_num\\tword_num\\t"
        "left\\ttop\\twidth\\theight\\tconf\\ttext')\n"
        f"print('5\\t1\\t1\\t1\\t1\\t1\\t20\\t10\\t60\\t12\\t{conf}\\t{text}')\n"
    )
    stub.chmod(0o755)
    return TesseractOCREngine(tesseract_bin=str(stub), upscales=(1,))


# -- RegionOCRCache --------------------------------------------------------------


def test_region_cache_hit_survives_change_outside_the_region():
    cache = RegionOCRCache()
    region = OCRRegion(10, 10, 40, 20)
    crop_a = np.zeros((20, 40, 3), dtype=np.uint8)
    screen = (1280, 800)
    key_a = cache.make_key(region, crop_a, screen, "cfg")
    cache.put(key_a, [OCRResult("File", (10, 10, 50, 30), 0.9)])

    # Same region pixels, different frame: the key must not change, so the entry
    # is a hit even though the rest of the screen moved.
    key_b = cache.make_key(region, crop_a.copy(), screen, "cfg")
    assert cache.get(key_b) == [OCRResult("File", (10, 10, 50, 30), 0.9)]
    assert cache.stats.hits == 1


def test_region_cache_key_includes_offset_pixels_and_config():
    cache = RegionOCRCache()
    crop = np.zeros((20, 40, 3), dtype=np.uint8)
    base = cache.make_key(OCRRegion(10, 10, 40, 20), crop, (1280, 800), "cfg")
    # Identical pixels at a different offset must be a distinct entry.
    assert base != cache.make_key(OCRRegion(11, 10, 40, 20), crop, (1280, 800), "cfg")
    # Different OCR config must be a distinct entry.
    assert base != cache.make_key(OCRRegion(10, 10, 40, 20), crop, (1280, 800), "cfg2")
    # Different pixels must be a distinct entry.
    assert base != cache.make_key(
        OCRRegion(10, 10, 40, 20), crop + 1, (1280, 800), "cfg"
    )


def test_region_cache_is_bounded_lru():
    cache = RegionOCRCache(max_entries=2)
    crop = np.zeros((4, 4, 3), dtype=np.uint8)
    for i in range(3):
        key = cache.make_key(OCRRegion(i, 0, 4, 4), crop, (100, 100), "cfg")
        cache.put(key, [OCRResult(f"w{i}", (i, 0, i + 4, 4), 0.9)])
    assert len(cache) == 2
    assert cache.stats.evictions == 1


def test_region_cache_get_returns_copies():
    cache = RegionOCRCache()
    region = OCRRegion(0, 0, 10, 10)
    crop = np.zeros((10, 10, 3), dtype=np.uint8)
    key = cache.make_key(region, crop, (100, 100), "cfg")
    cache.put(key, [OCRResult("x", (0, 0, 10, 10), 0.5)])
    got = cache.get(key)
    got[0].text = "mutated"
    assert cache.get(key)[0].text == "x"


# -- Region OCR -----------------------------------------------------------------


def test_extract_region_text_offsets_boxes_into_screen_space(tmp_path):
    engine = _stub_engine(tmp_path, text="File")
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    frame = _frame(pixels)
    results = engine.extract_region_text(frame, OCRRegion(300, 100, 200, 60))
    # Stub emits a box at (20,10)-(80,22) inside the crop; add the region offset.
    boxes = {r.text: r.bounding_box for r in results}
    assert boxes["File"] == (320, 110, 380, 122)


def test_region_ocr_result_cached_but_full_frame_hash_change_does_not_evict(tmp_path):
    engine = _stub_engine(tmp_path, text="Edit")
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    region = OCRRegion(100, 100, 300, 200)
    engine.extract_region_text(_frame(pixels, "frame-1"), region)
    before = engine.metrics_snapshot()
    # A different whole-frame hash with the same region pixels must be served from
    # the region cache: no new tesseract invocation.
    engine.extract_region_text(_frame(pixels, "frame-2"), region)
    after = engine.metrics_snapshot()
    assert after["ocr_cache_hits"] == before["ocr_cache_hits"] + 1
    assert after["ocr_cache_misses"] == before["ocr_cache_misses"]


def test_region_ocr_metrics_track_full_frame_vs_region_calls(tmp_path):
    engine = _stub_engine(tmp_path, text="View")
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    engine.extract_text_regions(_frame(pixels, "full"))
    engine.extract_region_text(_frame(pixels, "reg"), OCRRegion(0, 0, 400, 200))
    metrics = engine.metrics_snapshot()
    assert metrics["ocr_full_frame_calls"] == 1
    assert metrics["ocr_region_calls"] == 1
    assert metrics["ocr_full_frame_ms"] > 0
    assert metrics["ocr_region_ms"] > 0


def test_region_from_box_expands_and_clamps():
    region = VisualGrounder.region_from_box((10, 10, 30, 20), (100, 100), margin=5)
    assert region.to_tuple() == (5, 5, 30, 20)
    # Box touching the screen edge clamps instead of going negative.
    edge = VisualGrounder.region_from_box((0, 0, 10, 10), (100, 100), margin=20)
    assert edge.x == 0 and edge.y == 0


# -- Grounding ------------------------------------------------------------------


class _FakeOCREngine:
    """OCR engine whose region and full-frame paths return controlled matches."""

    def __init__(self, region_matches, full_matches, containment=False):
        self.region_matches = region_matches
        self.full_matches = full_matches
        self.containment = containment
        self.region_calls = 0
        self.full_calls = 0

    def find_text_in_region(self, frame, query, region, fuzzy=True, min_confidence=0.7, cache=True):
        self.region_calls += 1
        return list(self.region_matches)

    def find_text(self, frame, query, fuzzy=True, min_confidence=0.7):
        self.full_calls += 1
        return list(self.full_matches)

    def has_containment_match(self, query, matches):
        return self.containment


def test_confident_region_hint_avoids_full_frame_read():
    region_match = OCRResult("File", (330, 170, 350, 184), 0.9)
    engine = _FakeOCREngine([region_match], full_matches=[])
    grounder = VisualGrounder(ocr_engine=engine)
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    target = grounder.ground_query(
        _frame(pixels), "File", region_hint=OCRRegion(300, 145, 80, 70)
    )
    assert target.grounding_source == "region"
    assert engine.full_calls == 0
    assert target.coordinates == (340, 177)


def test_weak_region_hint_falls_back_to_full_frame():
    engine = _FakeOCREngine(
        [OCRResult("File", (330, 170, 350, 184), 0.4)],
        [OCRResult("File", (330, 170, 350, 184), 0.95)],
        containment=False,
    )
    grounder = VisualGrounder(ocr_engine=engine)
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    target = grounder.ground_query(
        _frame(pixels), "File", region_hint=OCRRegion(300, 145, 80, 70)
    )
    assert target.grounding_source == "full_frame"
    assert engine.full_calls == 1


def test_strong_containment_in_region_is_trusted_over_low_confidence():
    engine = _FakeOCREngine(
        [OCRResult("NEURO12A-GUI-010", (10, 10, 120, 22), 0.5)],
        full_matches=[],
        containment=True,
    )
    grounder = VisualGrounder(ocr_engine=engine)
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    target = grounder.ground_query(
        _frame(pixels), "NEURO12A-GUI-010", region_hint=OCRRegion(0, 0, 200, 100)
    )
    assert target.grounding_source == "region"
    assert engine.full_calls == 0


def test_stale_region_hint_with_no_match_falls_back_to_full_frame():
    engine = _FakeOCREngine(
        [],
        [OCRResult("File", (330, 170, 350, 184), 0.95)],
    )
    grounder = VisualGrounder(ocr_engine=engine)
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    target = grounder.ground_query(
        _frame(pixels), "File", region_hint=OCRRegion(0, 0, 50, 50)
    )
    assert target.grounding_source == "full_frame"
    assert target.coordinates == (340, 177)


def test_grounding_records_last_target_for_reuse():
    engine = _FakeOCREngine([OCRResult("File", (10, 10, 30, 20), 0.9)], [])
    grounder = VisualGrounder(ocr_engine=engine)
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    grounder.ground_query(_frame(pixels), "File", region_hint=OCRRegion(0, 0, 60, 60))
    assert grounder.last_target is not None
    assert grounder.last_target.query == "File"


@pytest.mark.parametrize("hint", [None, OCRRegion(0, 0, 60, 60)])
def test_region_and_full_frame_resolve_identical_coordinates(tmp_path, hint):
    engine = _stub_engine(tmp_path, text="File")
    pixels = np.zeros((800, 1280, 3), dtype=np.uint8)
    grounder = VisualGrounder(ocr_engine=engine)
    # Full-frame match first.
    full = VisualGrounder(ocr_engine=_stub_engine(tmp_path, text="File")).ground_query(
        _frame(pixels, "full"), "File"
    )
    region_target = grounder.ground_query(_frame(pixels, "reg"), "File", region_hint=hint)
    assert region_target.coordinates == full.coordinates
