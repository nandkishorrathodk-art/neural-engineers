"""Measure the real per-step cost of the ANCOS GUI loop components.

Runs the *actual* production classes (``TesseractOCREngine`` at its real
``_OCR_UPSCALES``, ``VisualGrounder``, ``ScreenChangeDetector``) against a rendered
1280x800 desktop-like frame. It excludes scrot/xdotool/Docker transport, which is
additional in a live run.

Usage:
    python docs/neuro/evidence/neuro13_stage1_latency_2026-11-14/measure_phases.py

This is a measurement harness, not a test. It is kept so the numbers in
``NEURO13_STAGE1_LATENCY_REPORT.md`` can be reproduced.
"""

from __future__ import annotations

import io
import statistics
import sys
import time

sys.path.insert(0, "services/core")

import numpy as np
from neural.gui_intelligence.models import ScreenFrame
from neural.gui_intelligence.perception.ocr_runtime import TesseractOCREngine
from neural.gui_intelligence.perception.screen_change import ScreenChangeDetector
from neural.gui_intelligence.perception.visual_grounding import VisualGrounder
from PIL import Image, ImageDraw

WIDTH, HEIGHT = 1280, 800
SAMPLES = 5


def render_desktop(seed: int) -> np.ndarray:
    """Render a desktop-like frame: menu bar, panel, document title and body text."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (238, 238, 236))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, WIDTH, 24], fill=(40, 40, 40))
    draw.text((8, 6), "File  Edit  View  Search  Tools  Help", fill=(230, 230, 230))
    draw.rectangle([0, 700, WIDTH, 800], fill=(60, 60, 60))
    draw.text(
        (10, 710),
        "Applications   Web Browser   File Manager   Terminal",
        fill=(230, 230, 230),
    )
    draw.text((120, 90), "handoff.txt - Mousepad", fill=(10, 10, 10))
    for index, line in enumerate(
        [
            "ANCOS_HYBRID_TEST",
            "TERMINAL_PREPARED=1",
            "GUI_EDIT=HYBRID_GUI_OK",
            "Save   Save As   Open   Recent Files",
            "Line 1   Column 1   UTF-8",
        ]
    ):
        draw.text((60, 160 + index * 28), line, fill=(10, 10, 10))
    for index in range(12):
        draw.text(
            (60, 340 + index * 22),
            f"body text line {index:02d} with some words to read {seed}",
            fill=(30, 30, 30),
        )
    return np.asarray(image, dtype=np.uint8)


def make_frame(seed: int, frame_hash: str) -> ScreenFrame:
    return ScreenFrame(
        timestamp=time.time(),
        resolution=(WIDTH, HEIGHT),
        pixels=render_desktop(seed),
        frame_hash=frame_hash,
    )


def timed(fn, *args, **kwargs) -> float:
    start = time.perf_counter()
    fn(*args, **kwargs)
    return (time.perf_counter() - start) * 1000.0


def main() -> None:
    engine = TesseractOCREngine()
    grounder = VisualGrounder(ocr_engine=engine)
    detector = ScreenChangeDetector()
    print(f"tesseract available: {engine.available}")

    full_ocr = [
        timed(engine.extract_text_regions, make_frame(i, f"full{i}")) for i in range(SAMPLES)
    ]
    print(f"FULL-frame OCR (cold): median={statistics.median(full_ocr):.0f}ms")

    region_ocr = []
    for i in range(SAMPLES):
        frame = make_frame(i, f"reg{i}")
        crop = frame.pixels[140:440, 40:440]
        region = ScreenFrame(
            timestamp=time.time(),
            resolution=(400, 300),
            pixels=crop,
            frame_hash=f"crop{i}",
        )
        region_ocr.append(timed(engine.extract_text_regions, region))
    print(f"REGION OCR 400x300 (cold): median={statistics.median(region_ocr):.0f}ms")

    grounding = [
        timed(grounder.ground_query, make_frame(i, f"gnd{i}"), "GUI_EDIT=HYBRID_GUI_OK")
        for i in range(SAMPLES)
    ]
    print(f"ground_query (fresh frame): median={statistics.median(grounding):.0f}ms")

    before = make_frame(0, "a")
    after = make_frame(1, "b")
    deltas = [timed(detector.compute_delta, before, after) for _ in range(20)]
    print(f"compute_delta (in-process): median={statistics.median(deltas):.1f}ms")

    buffer = io.BytesIO()
    Image.fromarray(render_desktop(0)).save(buffer, format="PNG")
    payload = buffer.getvalue()

    def decode() -> None:
        with Image.open(io.BytesIO(payload)) as image:
            np.asarray(image.convert("RGB"), dtype=np.uint8)

    decodes = [timed(decode) for _ in range(20)]
    print(f"PNG decode 1280x800: median={statistics.median(decodes):.1f}ms ({len(payload) // 1024}KB)")


if __name__ == "__main__":
    main()
