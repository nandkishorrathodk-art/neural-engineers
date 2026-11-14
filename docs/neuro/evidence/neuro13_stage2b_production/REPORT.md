# NEURO-13 Stage 2B — Production Runtime Validation

Date: 2026-11-14
Scope: validate the real production GUI path (broker → live container) and the
Stage 2 OCR optimization against it. No mocks, no shims.

## What was actually run

The harness `measure_stage2b_production.py` drives the **real** production path
end to end:

```
GuiWorkerEngine.execute
  -> ContainerDesktopDriver            (scrot capture, xdotool motor)
  -> ContainerManager.execute_command_sync
  -> RestrictedDockerBroker            (real HTTP broker API + real bearer token)
  -> live employee container           (ancos/employee-base, Xfce + TigerVNC)
```

Everything is real: the transport is the restricted broker, the desktop is a real
container (`ancos-desktop-anc-emp-repro0001`, DISPLAY=:1, 1280x800), OCR is real
Tesseract on real pixels, and verification is the independent visual verifier.

Command:

```bash
.venv/bin/python docs/neuro/evidence/neuro13_stage2b_production/measure_stage2b_production.py \
    --gui-count 10 --hybrid-count 10
```

## Results

### Reliability — 20 / 20 (100%)

| Objective class | Runs | Success + independently verified |
|---|---|---|
| GUI (`open text editor, type marker`) | 10 | **10** |
| Hybrid (terminal create + GUI edit + save) | 10 | **10** |

Every run was confirmed by the independent visual verifier reading the marker
from screen pixels; every hybrid run additionally had the payload confirmed on
disk in the container (`artifact_contains_payload = true`).

### Latency (live broker, P50 / P95 ms)

| Class | run P50 | run P95 | grounding P50 | grounding P95 |
|---|---|---|---|---|
| GUI | 10377 | 10550 | 3321 | 3398 |
| Hybrid | 22140 | 22549 | 4542 | 4875 |

### OCR path mix on real plans — the honest headline

| Class | full-frame OCR calls | **region OCR calls** | cache hits | cache misses |
|---|---|---|---|---|
| GUI (30 groundings) | 30 | **0** | 0 | 30 |
| Hybrid (30 groundings) | 30 | **0** | 0 | 30 |

**Region hints were used zero times on real production plans.** This is not a
regression; it is a property of the production planner: the deterministic GUI
plan (`derive_gui_steps`) and the document-edit choreography
(`derive_document_edit_steps`) **never repeat a target** within a plan — every
target (`Applications`, `Accessories`, `Mousepad`, `File`) appears at most once.
A region hint can only be created after a target has been grounded once, so a
first pass over a plan with no repeated target never populates or uses a hint.
Confirmed across a battery of objectives in `planner_target_sweep.json`
(`repeated_target: false` for all of them).

### Where region hints DO pay off (real, measured)

Region hints are reachable only when the *same* target is grounded again: a
settle-retry, a re-dispatch, or a later step that returns to the same control.
Two live measurements isolate that case.

**(a) Region-hint grounding A/B on live pixels** — target grounded, then an
off-region window appears (real screen change), then re-grounded:

| Mode | 2nd grounding | source |
|---|---|---|
| hints OFF | ~934 ms (median) | full_frame |
| hints ON | ~221 ms (median) | region |

**4.19× faster**, with the same target resolved (the ±5 px shift is
region-vs-frame centroid on the same button).

**(b) Worker-level repeated-target plan** (two clicks on the same label on the
live desktop, through `GuiWorkerEngine.execute`):

| Mode | grounding_ms | region calls |
|---|---|---|
| hints OFF | 1757 | 0 |
| hints ON | 1358 | 1 |

**1.29× faster** end-to-end grounding.

### Cold vs warm region OCR (live screen)

| Measurement | Median |
|---|---|
| full frame, cold (raw multi-scale OCR) | 1065 ms |
| region crop, cold (raw multi-scale OCR) | 430 ms |
| region, warm (region cache hit) | **0.29 ms** |

A region crop is **2.48×** cheaper than a full frame cold, and a warm region
cache hit is effectively free (~0.29 ms).

## Findings that matter beyond the speedup

1. **The Stage 2 speedup is real but narrow.** Region hints accelerate
   *repeated-target* grounding by 4.19× (grounding) / 1.29× (worker). On the
   current deterministic production plans, which have no repeated targets, they
   never engage, so they contribute **no** speedup to the 20/20 runs above. The
   OCR work that *does* run on every real plan is the full-frame read.

2. **The pre-existing whole-frame cache wins on a static screen.** The desktop
   is static between actions (the cursor is not captured), so an unchanged screen
   is already answered in ~6 ms by the whole-frame hash cache the engine already
   had. Region hints only win once something *outside* the target region changes —
   which is the realistic post-action case measured in (a) above.

3. **OCR recall on dense markers is the real limit, and it is latent.**
   The production payload `GUI_EDIT=HYBRID_GUI_OK` verified 10/10, and the most
   recent production GUI tasks with digit markers (`NEURO12A-GUI-001/002/003`)
   completed successfully — so ordinary markers verify fine. But a *dense*
   alphanumeric marker can be misread: `HYBRID13B_GUI_002=OK` was read by
   Tesseract as `HYBRID13B GUI @02=0K` (one digit `0`→`@`), and the matcher's
   digit guard then correctly rejects the near-match, so a *correct* on-screen
   deliverable fails verification. The guard is doing the right thing (it is what
   stops a *wrong* marker from passing); the limit is OCR recall on long
   digit-heavy tokens. No current production objective uses such a marker, so this
   is latent, not active.

4. **Hardening gap in the running workstation.** The live container reports
   `CapEff: 0000000000000000` (so all caps are dropped) and no Docker socket, but
   `NoNewPrivs: 0` and, from the host, `SecurityOpt=[]`, `CapDrop=[]`. The broker's
   provision path (`broker_api._provision_single_container`) *does* set
   `cap_drop=["ALL"]` and `security_opt=["no-new-privileges:true"]`, and the
   container's labels lack the broker's own labels (`ancos.employee_id`,
   `ancos.workspace_volume`, ...). The running container was therefore **not
   created by the current broker path** — it is a pre-existing/reprovisioned
   workstation, so it does not reflect the broker's hardening. Fresh
   broker-provisioned containers get the full profile; this one should be
   recreated to match. Reported, not silently "fixed".

## Conclusion

- The real production GUI path (broker + live container) works: **20/20** GUI and
  hybrid objectives succeeded and were independently verified.
- Stage 2's region OCR is a genuine **4.19×** (grounding) / **1.29×** (worker)
  win **for repeated-target grounding**, with a warm cache hit at ~0.29 ms.
- On the current production plans it engages **zero** times, because those plans
  never repeat a target. The optimization is correct and validated, but its
  production impact is currently dormant. Making it engage would require either
  plans that revisit a control, or extending hints to cover "same app / same
  region" across steps — a product decision, not a bug fix.

## Evidence files

- `measure_stage2b_production.py` — the harness (real broker, real container).
- `production_results.json` — raw results (reliability, latency, OCR mix, A/B).
- `planner_target_sweep.json` — proof that production plans have no repeated targets.
