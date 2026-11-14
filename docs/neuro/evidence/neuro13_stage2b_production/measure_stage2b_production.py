#!/usr/bin/env python3
"""NEURO-13 Stage 2B — production runtime validation.

Drives the *real* production path end to end:

    GuiWorkerEngine.execute
      -> ContainerDesktopDriver
      -> ContainerManager.execute_command_sync
      -> RestrictedDockerBroker (real HTTP broker API, real token)
      -> live employee container (ancos/employee-base:latest, Xfce + TigerVNC)

Nothing here is mocked or shimmed: the transport is the real broker, the desktop
is a real container, OCR is real Tesseract on real pixels.

It measures, over a battery of objectives:
  * reliability (status + independent visual verification pass rate),
  * P50/P95 wall latency and grounding latency,
  * the OCR path mix (full-frame vs region calls, cache hits),
  * cold vs warm region-OCR cache latency on the live screen,
  * region-hint grounding speedup on a repeated-target plan,
  * the GUI-only execution contract of the live container.

Usage:
    python measure_stage2b_production.py --container ancos-desktop-anc-emp-repro0001
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "services" / "core"))


def load_env() -> None:
    env_file = REPO / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # Override: the caller's shell may carry a corrupted copy of a JSON value.
        os.environ[key] = value


def broker_url() -> str:
    """Resolve the broker from inside the core-api container network."""
    override = os.environ.get("ANCOS_BROKER_URL_OVERRIDE")
    if override:
        return override
    import subprocess

    ip = subprocess.run(
        ["sudo", "-n", "docker", "inspect", "-f",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", "ancos-docker-broker"],
        capture_output=True, text=True,
    ).stdout.split()
    for candidate in ip:
        if candidate.startswith("172."):
            return f"http://{candidate}:12002"
    raise RuntimeError("could not resolve the broker address")


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="ancos-desktop-anc-emp-repro0001")
    parser.add_argument("--gui-count", type=int, default=10)
    parser.add_argument("--hybrid-count", type=int, default=10)
    parser.add_argument("--out", default=str(Path(__file__).parent / "production_results.json"))
    args = parser.parse_args()

    load_env()
    os.environ["DOCKER_BROKER_URL"] = broker_url()

    from neural.containers.manager import ContainerManager
    from neural.gui_intelligence.runtime.planner import derive_gui_steps, expected_onscreen_text
    from neural.gui_intelligence.worker import GuiStepPlan, GuiWorkerEngine

    cm = ContainerManager()
    assert cm._has_remote_broker(), "restricted broker is not configured"

    results: dict[str, object] = {
        "broker_url": os.environ["DOCKER_BROKER_URL"],
        "container": args.container,
    }

    # ---- GUI-only contract of the live container -------------------------------
    contract = {}
    probe = cm.execute_command_sync(args.container, [
        "sh", "-lc",
        "id -u; id -g; "
        "grep -E 'CapEff|NoNewPrivs' /proc/self/status; "
        "test -S /var/run/docker.sock && echo docker_sock_present || echo docker_sock_absent; "
        "command -v scrot xdotool mousepad tesseract",
    ])
    contract["raw"] = probe.get("stdout", "")
    results["gui_only_contract"] = contract

    # ---- cold vs warm region OCR + full-frame vs region on live pixels ---------
    from neural.gui_intelligence.perception.ocr_cache import OCRRegion
    from neural.gui_intelligence.perception.ocr_runtime import TesseractOCREngine
    from neural.gui_intelligence.runtime.desktop_driver import ContainerDesktopDriver

    driver = ContainerDesktopDriver(cm, args.container, display=":1")
    driver.assert_available()
    engine = TesseractOCREngine()
    region = OCRRegion(x=0, y=0, width=640, height=200)

    full_times, region_cold, region_warm = [], [], []
    for i in range(5):
        # Force a genuine cold read by calling the raw multi-scale OCR directly,
        # bypassing both the whole-frame and region caches, so this compares the
        # real tesseract cost of a full frame against a crop.
        fr = driver.capture_frame()
        t = time.perf_counter()
        engine._ocr_pixels(fr.pixels)
        full_times.append((time.perf_counter() - t) * 1000.0)

        crop = fr.pixels[region.y : region.y + region.height, region.x : region.x + region.width]
        t = time.perf_counter()
        engine._ocr_pixels(crop)
        region_cold.append((time.perf_counter() - t) * 1000.0)

        f3 = driver.capture_frame()
        engine.extract_region_text(f3, region, cache=True)  # prime the region cache
        t = time.perf_counter()
        engine.extract_region_text(f3, region, cache=True)
        region_warm.append((time.perf_counter() - t) * 1000.0)

    results["ocr_cold_warm"] = {
        "full_frame_median_ms": round(statistics.median(full_times), 1),
        "region_cold_median_ms": round(statistics.median(region_cold), 1),
        "region_warm_median_ms": round(statistics.median(region_warm), 3),
        "region_vs_full_speedup": round(
            statistics.median(full_times) / max(0.001, statistics.median(region_cold)), 2
        ),
    }

    # ---- region-hint grounding A/B on the live desktop (repeated target) -------
    from neural.gui_intelligence.perception.visual_grounding import VisualGrounder

    # Between the two groundings the desktop changes *outside* the target region
    # (the cursor moves, as it does after every click). The whole-frame hash cache
    # the engine already had is keyed on the full frame, so it misses; the region
    # cache is keyed on the region's own pixels, so it still hits. This is the
    # realistic production case: a repeated target re-grounded after an action.
    ab = {"off": [], "on": []}
    for mode in (False, True):
        for _ in range(3):
            _reset_desktop(cm, args.container)
            g = VisualGrounder(ocr_engine=TesseractOCREngine())
            f, grounded = _ground_retry(g, driver, "Applications")
            t = time.perf_counter()
            g.ground_query(f, "Applications")
            first_ms = (time.perf_counter() - t) * 1000.0
            hint = None
            if mode and grounded is not None and grounded.bounding_box != (0, 0, 0, 0):
                hint = VisualGrounder.region_from_box(grounded.bounding_box, f.resolution)
            # Change pixels outside the target region by popping a terminal window in
            # the lower-right, far from the top-left "Applications" button.
            cm.execute_command_sync(
                args.container,
                ["sh", "-lc",
                 "DISPLAY=:1 xfce4-terminal --geometry=50x10+880+560 --title=ancos-ab 2>/dev/null & sleep 2; true"],
            )
            f2 = driver.capture_frame()
            t = time.perf_counter()
            target = g.ground_query(f2, "Applications", region_hint=hint)
            second_ms = (time.perf_counter() - t) * 1000.0
            cm.execute_command_sync(args.container, ["sh", "-lc", "pkill -x xfce4-terminal; true"])
            ab["on" if mode else "off"].append(
                {
                    "first_ms": round(first_ms, 1),
                    "second_ms": round(second_ms, 1),
                    "first_coords": getattr(grounded, "coordinates", None),
                    "second_coords": getattr(target, "coordinates", None),
                    "source": getattr(target, "grounding_source", None),
                }
            )
    results["region_hint_ab"] = ab
    results["region_hint_ab_speedup"] = round(
        statistics.median([r["second_ms"] for r in ab["off"]])
        / max(0.001, statistics.median([r["second_ms"] for r in ab["on"]])),
        2,
    )

    # ---- worker-level A/B on a repeated-target plan (retry / re-ground path) ---
    # The deterministic production planner never repeats a target, so region hints
    # are dormant on a first pass. They pay off exactly when the same target is
    # grounded again — a settle-retry, a re-dispatch, or a later step that returns
    # to the same control. This models that with a two-step same-target plan on the
    # live desktop, which is the only path in the worker that reuses a region hint.
    worker_ab = {}
    for mode in (False, True):
        _reset_desktop(cm, args.container)
        # Open a real editor window so "File" is a stable, present menu label that
        # can be grounded twice without a side effect that removes it.
        cm.execute_command_sync(args.container, [
            "sh", "-lc",
            "pkill -9 -x mousepad; sleep 0.5; rm -rf /home/employee/.local/share/Mousepad; "
            "printf 'ANCOS_AB\\n' > /home/employee/workspace/ab.txt; "
            "DISPLAY=:1 mousepad /home/employee/workspace/ab.txt & sleep 3",
        ])
        eng = GuiWorkerEngine(container_manager=cm, display=":1", enable_region_hints=mode)
        steps = [
            GuiStepPlan(action_type="MOUSE_CLICK", target="File", confidence=0.95),
            GuiStepPlan(action_type="MOUSE_CLICK", target="File", confidence=0.95),
        ]
        try:
            res = eng.execute(
                employee_id="ANC-EMP-REPRO0001",
                docker_id=args.container,
                steps=steps,
                min_delta_magnitude=0.0001,
            )
            worker_ab["on" if mode else "off"] = {
                "grounding_ms": round(res.timings.get("grounding_ms", 0.0), 1),
                "ocr_metrics": {k: round(v, 1) for k, v in res.ocr_metrics.items()},
            }
        except Exception as exc:
            worker_ab["on" if mode else "off"] = {"error": str(exc)[:200]}
        _escape(cm, args.container)
    if "grounding_ms" in worker_ab.get("off", {}) and "grounding_ms" in worker_ab.get("on", {}):
        worker_ab["speedup"] = round(
            worker_ab["off"]["grounding_ms"] / max(0.001, worker_ab["on"]["grounding_ms"]), 2
        )
    cm.execute_command_sync(args.container, ["sh", "-lc", "pkill -9 -x mousepad; true"])
    results["worker_repeated_target_ab"] = worker_ab

    # ---- 10 GUI objectives ------------------------------------------------------
    gui_runs = []
    for i in range(1, args.gui_count + 1):
        _reset_desktop(cm, args.container)
        marker = f"NEURO13B-GUI-{i:03d}"
        title = f"Open text editor and type {marker}"
        objective = (
            "Launch the default text editor via the graphical desktop interface "
            f"and type the exact string {marker} into a new document."
        )
        steps_raw = derive_gui_steps(title, objective)
        steps = [GuiStepPlan(**s) for s in steps_raw]
        eng = GuiWorkerEngine(container_manager=cm, display=":1")
        t0 = time.perf_counter()
        try:
            res = eng.execute(
                employee_id="ANC-EMP-REPRO0001",
                docker_id=args.container,
                steps=steps,
                expected_text=expected_onscreen_text(steps_raw),
            )
            d = res.to_dict()
            gui_runs.append({
                "marker": marker,
                "status": d["status"],
                "verified": d["verification"]["passed"],
                "steps_executed": d["steps_executed"],
                "run_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                "grounding_ms": round(d["timings"].get("grounding_ms", 0.0), 1),
                "ocr_metrics": {k: round(v, 1) for k, v in d.get("ocr_metrics", {}).items()},
                "error": d.get("error"),
            })
        except Exception as exc:  # a real run may legitimately fail; record it
            gui_runs.append({"marker": marker, "status": "exception", "error": str(exc)[:300]})
        _escape(cm, args.container)
    results["gui_objectives"] = gui_runs

    # ---- 10 hybrid objectives (GUI document edit + terminal artifact check) -----
    # The payload is the one real ANCOS hybrid tasks use (GUI_EDIT=HYBRID_GUI_OK),
    # so this measures the production choreography, not a synthetic marker.
    hybrid_runs = []
    for i in range(1, args.hybrid_count + 1):
        _reset_desktop(cm, args.container)
        payload = "GUI_EDIT=HYBRID_GUI_OK"
        title = f"Create and edit handoff{i:03d}.txt via hybrid workflow"
        objective = (
            f"Create handoff{i:03d}.txt in the workspace, then open it in the text "
            f"editor through the graphical desktop and append the line {payload}, "
            "then save."
        )
        acceptance = f"handoff{i:03d}.txt exists and contains {payload}"
        # Terminal (prepare) phase: create the artifact in the container workspace.
        cm.execute_command_sync(args.container, [
            "sh", "-lc",
            f"printf 'ANCOS_HYBRID_TEST\\nTERMINAL_PREPARED=1\\n' > handoff{i:03d}.txt",
        ])
        steps_raw = derive_gui_steps(title, objective, "", acceptance)
        steps = [GuiStepPlan(**s) for s in steps_raw]
        eng = GuiWorkerEngine(container_manager=cm, display=":1")
        t0 = time.perf_counter()
        try:
            res = eng.execute(
                employee_id="ANC-EMP-REPRO0001",
                docker_id=args.container,
                steps=steps,
                expected_text=expected_onscreen_text(steps_raw),
            )
            d = res.to_dict()
            check = cm.execute_command_sync(args.container, [
                "sh", "-lc", f"grep -c '{payload}' handoff{i:03d}.txt 2>/dev/null || echo 0",
            ])
            on_disk = str(check.get("stdout", "")).strip().endswith("1") or str(
                check.get("stdout", "")
            ).strip() == "1"
            hybrid_runs.append({
                "marker": payload,
                "gui_status": d["status"],
                "gui_verified": d["verification"]["passed"],
                "artifact_contains_payload": bool(on_disk),
                "steps_executed": d["steps_executed"],
                "run_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                "grounding_ms": round(d["timings"].get("grounding_ms", 0.0), 1),
                "ocr_metrics": {k: round(v, 1) for k, v in d.get("ocr_metrics", {}).items()},
                "error": d.get("error"),
            })
        except Exception as exc:
            hybrid_runs.append({"marker": payload, "gui_status": "exception", "error": str(exc)[:300]})
        _escape(cm, args.container)
    results["hybrid_objectives"] = hybrid_runs

    # ---- aggregates -------------------------------------------------------------
    def summarize(runs, status_key, verify_key):
        ok = [r for r in runs if r.get(status_key) == "success" and r.get(verify_key)]
        run_ms = [r["run_ms"] for r in runs if "run_ms" in r]
        ground_ms = [r["grounding_ms"] for r in runs if "grounding_ms" in r]
        region_calls = sum(r.get("ocr_metrics", {}).get("ocr_region_calls", 0) for r in runs)
        full_calls = sum(r.get("ocr_metrics", {}).get("ocr_full_frame_calls", 0) for r in runs)
        hits = sum(r.get("ocr_metrics", {}).get("ocr_cache_hits", 0) for r in runs)
        misses = sum(r.get("ocr_metrics", {}).get("ocr_cache_misses", 0) for r in runs)
        return {
            "runs": len(runs),
            "success_and_verified": len(ok),
            "reliability": round(len(ok) / len(runs), 3) if runs else 0.0,
            "run_ms_p50": pct(run_ms, 50),
            "run_ms_p95": pct(run_ms, 95),
            "grounding_ms_p50": pct(ground_ms, 50),
            "grounding_ms_p95": pct(ground_ms, 95),
            "ocr_full_frame_calls": full_calls,
            "ocr_region_calls": region_calls,
            "ocr_cache_hits": hits,
            "ocr_cache_misses": misses,
            "ocr_cache_hit_ratio": round(hits / (hits + misses), 3) if (hits + misses) else 0.0,
        }

    results["summary"] = {
        "gui": summarize(gui_runs, "status", "verified"),
        "hybrid": summarize(hybrid_runs, "gui_status", "gui_verified"),
    }

    Path(args.out).write_text(json.dumps(results, indent=2))
    print(json.dumps(results["summary"], indent=2))
    print(f"\nwrote {args.out}")
    return 0


def _escape(cm, container: str) -> None:
    """Close any open menu so the next objective starts from a clean desktop."""
    cm.execute_command_sync(container, ["sh", "-lc", "DISPLAY=:1 xdotool key Escape; true"])


def _reset_desktop(cm, container: str) -> None:
    """Close every application window so measurements start from a bare desktop.

    A leftover window (a text editor, a file manager, a stray terminal) changes
    what OCR sees and can cover the top-left "Applications" launcher, which makes
    a grounding return (-1, -1) and silently invalidates the A/B. Resetting keeps
    each measured section independent of whatever the previous one left behind.
    """
    cm.execute_command_sync(container, [
        "sh", "-lc",
        "pkill -9 -x mousepad; pkill -9 -x thunar; pkill -9 -x xfce4-terminal; "
        "pkill -9 -x xfce4-taskmanager; rm -rf /home/employee/.local/share/Mousepad; "
        "sleep 1.5; true",
    ])


def _ground_retry(grounder, driver, target: str, attempts: int = 4):
    """Ground ``target``, retrying while the desktop settles, or raise."""
    last = None
    for _ in range(attempts):
        frame = driver.capture_frame()
        last = grounder.ground_query(frame, target)
        if last is not None and tuple(getattr(last, "coordinates", (-1, -1))) != (-1, -1):
            return frame, last
        time.sleep(1.0)
    raise RuntimeError(f"could not ground '{target}' on the live desktop after {attempts} tries")


if __name__ == "__main__":
    raise SystemExit(main())
