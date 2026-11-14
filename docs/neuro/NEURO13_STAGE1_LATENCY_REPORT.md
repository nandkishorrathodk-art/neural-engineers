# NEURO-13 Stage 1 — Per-step latency instrumentation & measured bottleneck

**Date:** 2026-11-14. **Branch:** `main`. **Code state:** `e575997` + this commit.
**Scope:** measure, do not optimise. Stage 2 (OCR work) and Stage 3 (semantic channel)
are NOT part of this change.

## 1. What was added (instrumentation only)

No execution behaviour changed. Three additive fields:

- `GUIActionReceipt.timings: dict[str, float]` — per-action phase attribution in ms:
  `capture_ms` (pre-action screenshot), `dispatch_ms` (policy check + grounding + motor
  events, **exclusive** of settle sleeps), `settle_ms` (explicit `WAIT_SETTLE` sleep),
  `confirm_ms` (`CONFIRM_SAVED` app-state poll), `delta_ms` (post-action screenshot +
  change detection).
- `GuiWorkerResult.timings: dict[str, float]` — whole-run attribution: `grounding_ms`
  (per-step fresh-frame capture + OCR target search), `step_ms` (sum of per-action
  receipts), `verification_ms` (final independent verifier), `run_ms` (total).
- Both serialise through the existing `/internal/worker/tasks/{id}/gui/execute` response
  (`result.to_dict()` already returns the new `timings`).

Two regression tests lock this in:
`test_gui_action_receipt_reports_per_phase_timings` and
`test_worker_result_reports_phase_latency_attribution`. Full suite: **976 passed,
11 skipped**; `ruff check` clean.

## 2. Measured per-step cost (real components, real config)

The live API and Docker daemon are **not reachable from this analysis host**, so an
end-to-end `/gui/execute` run could not be executed here. What *was* measured is the
real cost of each component using the **actual production classes** (`TesseractOCREngine`
at its real `_OCR_UPSCALES = (2, 3)`, `VisualGrounder`, `ScreenChangeDetector`) on a
rendered 1280×800 desktop-like frame. `scrot`/`xdotool`/Docker transport time is
**excluded** and is additional in a live run.

| Phase | Measured | Notes |
|---|---|---|
| **Full-frame OCR (cold)** | **876–1154 ms** median across runs | 206 text regions; the dominant per-step cost |
| Region OCR 400×300 (cold) | 394–544 ms | Stage 2 candidate; ~2.1–2.2× faster than full frame |
| `ground_query` on a fresh frame | **1089–1206 ms** median | fresh capture ⇒ unique hash ⇒ OCR cache miss |
| Verify OCR on a fresh frame | ~6 ms | only cheap when the frame was already OCR'd |
| `compute_delta` (in-process) | 36–38 ms | pixel compare |
| PNG decode 1280×800 | ~9.5 ms | local part of capture only |
| Explicit settle sleeps (plan) | up to **5200 ms/task** | 2500+1200+1500 ms in the doc-edit plan |

Host: 4 vCPU AMD EPYC 9B14, 15 GB RAM, Tesseract 5.5.0, Python 3.13.15. Reproduce with
`evidence/neuro13_stage1_latency_2026-11-14/measure_phases.py`; raw output in
`evidence/neuro13_stage1_latency_2026-11-14/raw_measurement.txt`. The two OCR runs above
bracket run-to-run variance on a shared 4-vCPU host; the ratio is stable.

### The finding that corrects the premise (again)

The earlier phase premise said "5–10 s per step, LLM thinking too often". The
instrumentation and these measurements show the real structure:

1. **There is no LLM in the worker GUI path** — confirmed by import/grep. So no per-step
   inference exists to optimise.
2. **The real per-step cost is OCR.** A fresh-frame grounding OCR is ~1.1 s; the worker
   captures a *fresh* frame for every step (`_to_request_with_fresh_frame`), so the
   frame-hash OCR cache **misses on every step** by design.
3. Therefore "5–10 s per step" is explained by **~1.1 s OCR × (grounding + re-ground
   attempts, up to 6) + settle waits + broker transport**, not by model calls.

## 3. What this means for Stage 2

The measurement points at a cheap, contract-safe win: the OCR is done over the **entire
1280×800 frame at 2× and 3× upscale** even when only a small target region matters.
Region OCR of a 400×300 crop measured **2.2× faster** (394 ms vs 876 ms). That, plus not
re-OCRing unchanged frames, is Stage 2 — and it needs no change to the GUI-only contract.

## 4. Honest gaps

- **No end-to-end `/gui/execute` latency** was measured: no live API, no Docker daemon on
  this host. The phase timings are now *emitted*, but a live run must be captured to get
  the true per-step total (including `scrot` + `docker exec` transport).
- The OCR numbers are from a **synthetic rendered frame**, not a real Mousepad window.
  Glyph rendering and window complexity will shift them.
- `dispatch_ms` currently bundles policy + grounding + motor; motor (`xdotool`) cost is
  not separable from grounding without a live broker.
