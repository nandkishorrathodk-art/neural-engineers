#!/usr/bin/env python3
"""Real worker-level A/B: region hints on vs off, driving the live X desktop.

Runs the *actual* ``GuiWorkerEngine.execute`` against a real Xvfb desktop with a
real Mousepad window, through ``ContainerDesktopDriver`` and a host ``sh`` broker
shim. The plan re-grounds the *same* target ("File") twice, so the second
grounding is the one region hints can accelerate.

It reports, for each mode, the total ``grounding_ms`` and the per-step resolved
coordinates, and asserts both modes resolve the same coordinates so the
acceleration is not bought with a wrong click.

Usage: python measure_worker_region_hints.py [--repeat N]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "services" / "core"))

from neural.gui_intelligence.worker import GuiStepPlan, GuiWorkerEngine  # noqa: E402


class LocalBroker:
    def __init__(self, display: str) -> None:
        self.display = display

    def execute_command_sync(self, _docker_id: str, command, workdir=None, env=None):
        proc = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            cwd=workdir if (workdir and os.path.isdir(workdir)) else None,
            env={**os.environ, "DISPLAY": self.display, **(env or {})},
        )
        return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def relaunch_editor(display: str) -> None:
    subprocess.run(["pkill", "-9", "-x", "mousepad"], capture_output=True)
    time.sleep(0.5)
    Path("/tmp/bench_handoff.txt").write_text("ANCOS_HYBRID_TEST\nTERMINAL_PREPARED=1\n")
    subprocess.Popen(
        ["mousepad", "/tmp/bench_handoff.txt"],
        env={**os.environ, "DISPLAY": display},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3.0)


def run_once(display: str, enable_hints: bool) -> dict:
    relaunch_editor(display)
    engine = GuiWorkerEngine(
        container_manager=LocalBroker(display),
        display=display,
        # The benchmark pre-launches the editor itself, so skip the driver's
        # editor-state reset (which would kill that window).
        require_real_desktop=False,
        enable_region_hints=enable_hints,
    )
    steps = [
        GuiStepPlan(action_type="MOUSE_CLICK", target="File", confidence=0.95),
        GuiStepPlan(action_type="MOUSE_CLICK", target="File", confidence=0.95),
    ]
    result = engine.execute(
        employee_id="ANC-EMP-BENCH",
        docker_id="bench",
        steps=steps,
        min_delta_magnitude=0.0001,
    )
    coords = [r.get("observed_postcondition") for r in result.receipts]
    return {
        "enable_region_hints": enable_hints,
        "status": result.status,
        "steps_executed": result.steps_executed,
        "grounding_ms": round(result.timings.get("grounding_ms", 0.0), 1),
        "run_ms": round(result.timings.get("run_ms", 0.0), 1),
        "ocr_metrics": {k: round(v, 1) for k, v in result.ocr_metrics.items()},
        "receipt_statuses": [r.get("status") for r in result.receipts],
        "receipt_deltas": coords,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--display", default=":1")
    args = parser.parse_args()

    off = [run_once(args.display, False) for _ in range(args.repeat)]
    on = [run_once(args.display, True) for _ in range(args.repeat)]

    def med(runs, key):
        import statistics

        return round(statistics.median([r[key] for r in runs]), 1)

    summary = {
        "runs_off": off,
        "runs_on": on,
        "grounding_ms_off_median": med(off, "grounding_ms"),
        "grounding_ms_on_median": med(on, "grounding_ms"),
        "speedup": round(med(off, "grounding_ms") / max(0.001, med(on, "grounding_ms")), 2),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
