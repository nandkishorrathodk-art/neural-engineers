# NEURO-13 Stage 2 — OCR reliability, region caching, region-hint grounding

Stage 1 measured where GUI step time actually goes and found the dominant,
repeated cost is Tesseract OCR: ~1.15 s per full-frame read at two upscales, and
the same target is re-grounded from scratch on every step. Stage 2 removes that
redundant work without changing the strict GUI-only execution contract.

## What changed

1. **Bounded region OCR cache** (`perception/ocr_cache.py`, new)
   - Keyed on the **region's own pixels** plus its geometry and the OCR config
     signature, so an unrelated change elsewhere on screen (clock, cursor blink)
     no longer invalidates a region read.
   - LRU-bounded, returns copies so callers cannot mutate cached entries.

2. **Region OCR + telemetry** (`perception/ocr_runtime.py`, `TesseractOCREngine`)
   - `extract_region_text(frame, region, cache=True)` OCRs only a sub-rectangle
     and maps boxes back to screen coordinates.
   - `find_text_in_region(...)` matches a query inside a region.
   - `has_containment_match(...)`: a separator-insensitive textual match is
     trusted even when tesseract's per-word confidence dips on a crop.
   - Cumulative, per-call telemetry via `metrics_snapshot()`
     (`ocr_full_frame_ms`, `ocr_region_ms`, cache hits/misses/evictions).

3. **Region-hint visual grounding** (`perception/visual_grounding.py`)
   - `ground_query(frame, target, region_hint=...)` tries the region first and
     **only** trusts it when the *fused* grounding confidence clears the
     threshold or the region text contains the query; otherwise it falls back to
     a full-frame read. A stale or wrong hint can never produce a wrong click.
   - `last_target` exposes the last grounded target so the worker can reuse its
     box as the next hint.

4. **Worker wiring** (`worker.py`)
   - A target located once is re-grounded inside its last known region on the
     next step; switchable via `enable_region_hints` so the optimization can be
     A/B measured.
   - `ocr_metrics` are attached to `GuiWorkerResult` and `grounding_ms` is
     attributed in the timing breakdown.

## Measured on a real desktop (not synthetic)

Both benchmarks drive the real code against a real X desktop: **Xvfb 1280x800 +
a real Mousepad window**, frames from `scrot`, actions through `xdotool`, via
`ContainerDesktopDriver` and a host-`sh` broker shim. The only substitution is
the transport (local `sh` instead of the Docker broker); perception, grounding
and verification are the production paths. Docker itself is unusable in this
sandbox (socket permission denied), which is why a real local X stack is used
instead of a container.

### Single-target grounding (`region_grounding.json`)

| Path | Median latency | Source |
| --- | --- | --- |
| Full-frame grounding | 538.8 ms | `full_frame` |
| Region-hint grounding | 198.8 ms | `region` |
| **Cold speedup** | **2.71x** | |
| Warm region cache read | 0.017 ms | cache hit |

Resolved coordinates are **identical** in both paths (`(339, 178)`), so the
speedup is not bought with a wrong target.

### Worker end-to-end A/B (`worker_region_hints.json`)

Same plan, same live desktop, same repeated target; the only difference is
`enable_region_hints`:

| Mode | grounding total | full-frame OCR calls | region OCR calls |
| --- | --- | --- | --- |
| hints **off** | 1245.5 ms | 2 | 0 |
| hints **on** | 840.2 ms | 1 | 1 |

**1.48x faster grounding** at the worker level, with identical receipt deltas
(`delta_0.0106`) in both modes. The per-step ceiling is 2.71x; the worker number
is lower because the plan still needs one full-frame read to seed the region.

## Honest limitations

- The Docker path could not be exercised here (no Docker socket access in this
  sandbox), so the benchmark transport is a host `sh` shim, not the real
  container broker. The perception/grounding/verification code under test is the
  production code; the transport is the substituted part.
- The worker-level speedup (1.48x) is below the per-step ceiling (2.71x) because
  seeding a region still costs one full-frame read. Workloads that repeat a
  target benefit most.
- `has_containment_match` requires a normalized query of length >= 4, so very
  short labels fall back to the confidence gate.

## Verification

- `ruff check .` — All checks passed.
- Full suite — **991 passed, 11 skipped**.

Reproduce:

```bash
export DISPLAY=:1
python docs/neuro/evidence/neuro13_stage2_ocr_2026-11-14/measure_region_grounding.py --repeat 5
python docs/neuro/evidence/neuro13_stage2_ocr_2026-11-14/measure_worker_region_hints.py --repeat 3
```
