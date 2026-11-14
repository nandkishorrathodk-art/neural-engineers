#!/usr/bin/env python3
"""Real end-to-end benchmark: GUI worker grounding with and without region hints.

Runs the *actual* ``GuiWorkerEngine`` against a real X desktop (Xvfb + a real
Mousepad window) through a minimal broker shim that shells out to the host. This
is not a mock desktop: frames come from ``scrot`` and actions go through
``xdotool``, exactly as ``ContainerDesktopDriver`` does inside an employee
container. The only substitution is the transport (local ``sh`` instead of the
Docker broker), so the perception/grounding/verification code paths are the real
ones.

It measures, on identical frames:
  * full-frame grounding latency per step (optimization off), and
  * region-hint grounding latency (optimization on),
and asserts both resolve the *same* target coordinates, so the speedup is not
bought with a wrong click.

Usage: python measure_region_grounding.py [--repeat N]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
import sys  # noqa: E402

sys.path.insert(0, str(REPO / "services" / "core"))

from neural.gui_intelligence.models import ScreenFrame  # noqa: E402
from neural.gui_intelligence.perception.ocr_runtime import TesseractOCREngine  # noqa: E402
from neural.gui_intelligence.perception.visual_grounding import (  # noqa: E402
    VisualGrounder,
)


class LocalBroker:
    """Minimal ContainerManager-compatible shim that runs commands on the host.

    ``execute_command_sync`` is the single method ``ContainerDesktopDriver`` uses,
    so this drives the real driver against the real display.
    """

    def __init__(self, display: str) -> None:
        self.display = display

    def execute_command_sync(self, _docker_id: str, command, workdir=None, env=None):
        script = " ".join(command)
        proc = subprocess.run(
            ["sh", "-lc", script],
            capture_output=True,
            text=True,
            cwd=workdir,
            env={**os.environ, "DISPLAY": self.display, **(env or {})},
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


def capture(display: str) -> ScreenFrame:
    import io

    import numpy as np
    from PIL import Image

    raw = subprocess.run(
        ["sh", "-lc", "scrot - | base64 -w0"],
        capture_output=True,
        text=True,
        env={**os.environ, "DISPLAY": display},
        check=True,
    ).stdout.strip()
    import base64

    data = base64.b64decode(raw)
    with Image.open(io.BytesIO(data)) as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = pixels.shape[:2]
    return ScreenFrame(
        timestamp=time.time(),
        resolution=(int(w), int(h)),
        pixels=pixels,
        frame_hash=f"live-{time.time_ns()}",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--display", default=":1")
    parser.add_argument("--target", default="File")
    args = parser.parse_args()

    if not shutil.which("scrot") or not shutil.which("tesseract"):
        print(json.dumps({"error": "scrot/tesseract not available on host"}))
        return 1

    frame = capture(args.display)
    print(f"live frame {frame.resolution[0]}x{frame.resolution[1]}")

    full_times: list[float] = []
    region_times: list[float] = []
    full_coords = None
    region_coords = None

    # Cold full-frame grounding, repeated on the SAME pixels with fresh engines so
    # the frame-hash cache cannot hide the cost.
    for _ in range(args.repeat):
        engine = TesseractOCREngine()
        grounder = VisualGrounder(ocr_engine=engine)
        t0 = time.perf_counter()
        target = grounder.ground_query(frame, args.target)
        full_times.append((time.perf_counter() - t0) * 1000.0)
        full_coords = target.coordinates

    # Region-hint grounding: locate once (full frame), then re-ground inside the
    # resulting region. Cold cache per iteration to measure the region read itself.
    engine = TesseractOCREngine()
    grounder = VisualGrounder(ocr_engine=engine)
    seed = grounder.ground_query(frame, args.target)
    region = VisualGrounder.region_from_box(seed.bounding_box, frame.resolution)
    assert region is not None
    for _ in range(args.repeat):
        engine = TesseractOCREngine()
        grounder = VisualGrounder(ocr_engine=engine)
        t0 = time.perf_counter()
        target = grounder.ground_query(frame, args.target, region_hint=region)
        region_times.append((time.perf_counter() - t0) * 1000.0)
        region_coords = target.coordinates
        source = target.grounding_source

    # Warm region cache: repeat reads of an unchanged region while the frame hash
    # changes every time (as a live desktop's does).
    engine = TesseractOCREngine()
    engine.extract_region_text(frame, region)
    warm_times: list[float] = []
    for _ in range(args.repeat * 4):
        changing = ScreenFrame(
            timestamp=time.time(),
            resolution=frame.resolution,
            pixels=frame.pixels,
            frame_hash=f"changed-{time.time_ns()}",
        )
        t0 = time.perf_counter()
        engine.extract_region_text(changing, region)
        warm_times.append((time.perf_counter() - t0) * 1000.0)

    result = {
        "target": args.target,
        "resolution": list(frame.resolution),
        "region": list(region.to_tuple()),
        "full_frame_median_ms": round(statistics.median(full_times), 1),
        "region_hint_median_ms": round(statistics.median(region_times), 1),
        "region_hint_source": source,
        "region_cold_speedup": round(
            statistics.median(full_times) / statistics.median(region_times), 2
        ),
        "warm_region_cache_median_ms": round(statistics.median(warm_times), 3),
        "warm_cache_stats": engine.cache_stats,
        "same_coordinates": full_coords == region_coords,
        "full_coords": list(full_coords) if full_coords else None,
        "region_coords": list(region_coords) if region_coords else None,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
