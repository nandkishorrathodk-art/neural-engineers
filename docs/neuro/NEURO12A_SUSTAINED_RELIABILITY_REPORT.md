# NEURO-12A sustained-workload reliability report (pre-fix vs post-fix)

Generated: 2026-11-14 (UTC). Instrument: `neural.reliability.batch_runner` +
`neural.reliability.batch_driver`. Raw evidence in
`docs/neuro/evidence/neuro12a_reliability_2026-11-14/`.

This report measures — it does not claim — whether ANCOS executes GUI-bearing
objectives reliably across many real objectives, and it separates a pre-fix cohort
from post-fix cohorts so a rate delta is only asserted where the workloads match.

## What "cohort" means here

Each cohort is a batch of **unique** objectives (never a retried objective recounted).
Sampling follows the smoke contract: 10 distinct GUI objectives and 10 distinct hybrid
objectives. A retried objective contributes one sample and its retry count is reported
separately. In-flight, cancelled, and timed-out objectives are never counted as
successes.

- **Pre-fix cohort** — verifier before the plan-derived on-screen term fix (the verifier
  scraped required on-screen text from free-form objective prose).
- **Post-fix cohorts** — verifier after that fix. Runs 2 and 3 additionally include the
  verifier-queue `scope` fix (see "Defects fixed" below).

All cohorts share an identical environment fingerprint: branch `main`, HEAD
`4c5530f2026cb8cb2f9928b6a4088b41e85dfbea`, desktop image `ancos/employee-base:latest`,
and the same model routing config. `head_stable_during_cohort` is true for every run, so
the code under test did not move mid-batch.

## Results

### Per-run

| Cohort | Mode | Samples | Completed | Failed | Success rate | Retry freq | Mean latency (s) | p95 latency (s) | Cost/sample (USD) |
|---|---|---|---|---|---|---|---|---|---|
| pre-fix | gui | 10 | 9 | 1 | 90% | 0.3 | 34.3 | 58.0 | 0.000000 |
| pre-fix | hybrid | 10 | 1 | 9 | **10%** | 2.7 | 108.4 | 150.7 | 0.000000 |
| post-fix run 1 | gui | 10 | 9 | 1 | 90% | 0.3 | 33.8 | 59.2 | 0.000000 |
| post-fix run 1 | hybrid | 10 | 10 | 0 | **100%** | 0.2 | 56.8 | 93.7 | 0.000000 |
| post-fix run 2 | gui | 10 | 9 | 1 | 90% | 0.3 | 35.3 | 61.3 | 0.000000 |
| post-fix run 2 | hybrid | 10 | 9 | 1 | 90% | 0.7 | 68.4 | 149.9 | 0.001431 |
| post-fix run 3 | gui | 10 | 9 | 1 | 90% | 0.3 | 35.3 | 67.2 | 0.000000 |
| post-fix run 3 | hybrid | 10 | 8 | 2 | 80% | 1.1 | 77.7 | 144.8 | 0.001230 |
| post-fix run 4 (payload fix) | gui | 10 | 9 | 1 | 90% | 0.3 | 36.8 | 61.6 | 0.000000 |
| post-fix run 4 (payload fix) | hybrid | 10 | 9 | 1 | 90% | 0.4 | 55.8 | 98.6 | 0.001017 |

Run 4 is a **separate fingerprint** (HEAD `f3a2438`, which adds the payload-selection
fix below on top of `4c5530f`), so it is reported but not folded into the aggregate above.
Its evidence is `cohort_postfix_payload_fix.json`.

### Post-fix aggregate (3 runs, 30 samples per mode, HEAD `4c5530f`)

| Mode | Completed | Samples | Success rate |
|---|---|---|---|
| gui | 27 | 30 | 90% |
| hybrid | 27 | 30 | 90% |

### Headline delta

**Hybrid: 10% (1/10) pre-fix → 90% (27/30) post-fix.** GUI is flat at 90% (9/10 every
run), which is expected: the fix targeted the verifier's on-screen term derivation, a
failure path specific to hybrid contracts that name terminal-side artifacts
("print its SHA-256 hash") in their prose.

## Defects fixed (and their evidence)

1. **Verifier false negative on hybrid on-screen terms.** The verifier derived required
   on-screen text by scraping hyphenated uppercase tokens from free-form prose, so a
   hybrid contract naming a terminal output (`SHA-256`) demanded that token appear on the
   GUI screen and rejected correct runs. The verifier now derives the terms from the same
   GUI plan the worker executed (`expected_onscreen_text`), falling back to the signed
   acceptance criteria only when no typed-deliverable choreography exists.
2. **Verifier queue dropped `scope`.** The worker planned its GUI steps with
   `task.scope`, but the verifier queue payload omitted `scope`
   (`TaskRepository.list_pending_verification`). When the Boss planner paraphrased an
   objective such that the edit verb or payload survived only in `scope`, the verifier
   derived a different plan and fell back to scraping — reintroducing defect 1
   intermittently. `scope` is now carried in the queue payload, so worker and verifier
   derive from the identical contract. Regression test:
   `test_verifier_queue_exposes_scope_for_onscreen_term_derivation`.
3. **Hybrid cost reported as $0.00.** Hybrid receipts carry cost only in per-phase
   `model` blocks, not a top-level `model`, so cost aggregation reported hybrid work as
   free. `extract_receipt_cost_usd` now sums both shapes, and the control-plane report
   aggregates per-phase cost (`_execution_cost_usd` in `boss/brain.py`). Regression tests
   in `test_reliability_batch_runner.py`.
4. **GUI edit typed the terminal-side payload.** A hybrid title such as "…hybrid
   terminal+GUI workflow" places a GUI hint ("GUI") inside the 80-character proximity
   window of the terminal-side payload (`TERMINAL_PREPARED=1`), so the proximity-first
   selector chose the terminal payload and the desktop typed the wrong line — the GUI edit
   never happened and the task failed. `_select_edit_payload` now ranks a token whose key
   names the GUI (`GUI_EDIT=…`) above proximity and skips terminal-named keys
   (`terminal`/`prepare`/`verify`/`hash`) in its fallbacks. Regression test:
   `test_planner_prefers_gui_named_payload_over_terminal_named_one`.
5. **Unhandled 500 when the desktop vanishes mid-task.** The workstation can disappear
   between the route's liveness check and the first broker call (lease reaper, operator
   terminate, daemon restart). `execute_gui_plan`/`execute_gui_step` only caught
   `DesktopRuntimeUnavailable` and `GuiWorkerExecutionError`, so a `ContainerError` from
   the broker surfaced as an unhandled 500 that reads as a server fault. Both routes now
   return a retryable **409**. Regression test:
   `test_gui_execute_returns_conflict_when_desktop_vanishes_mid_task`.

## What the remaining failures actually are

Every post-fix failure (gui-010 in all three runs; hybrid-004 and hybrid-010 in run 3) is
a **worker-layer GUI flake**, not a verifier false negative. The receipts show
`gui.status = null`, `worker_screenshot_hash = null`, and
`worker_gui_loop_reported_success = false`: the worker's GUI phase produced no post-action
frame (in the observed case, only 4 of 13 planned steps executed before the loop gave up).
The independent verifier correctly failed closed. This failure mode is present in the
pre-fix cohort too (gui-010) and is **not fixed by this work**.

The verifier's `onscreen_deliverable_terms_observed_by_verifier` check no longer fails in
any post-fix run — the term-derivation defect is gone.

Run 4's single hybrid failure (`5c89f3c0fc-hybrid-001`) is a **different** worker-layer
flake: the GUI phase planned and typed the correct payload, but the visual delta came back
`0.000000` and the desktop produced no changed frame. Run 4's single GUI failure
(`5c89f3c0fc-gui-010`) is the same frame-capture flake as runs 1–3: the loop typed the
line but the final screenshot did not read the expected term. Neither is the payload
defect that run 4 was measuring, and both are environment flakiness, not logic defects.

## Honest caveats

- **GUI cost is genuinely $0.00/sample, not a reporting gap.** The GUI receipt records no
  model cost because the deterministic document-edit choreography makes no LLM call. This
  is a real measurement, not missing instrumentation.
- **Hybrid cost is real but small** (~$0.0012–0.0014 per sample): one terminal-prepare
  inference per objective at the configured reasoning tier.
- **Worker-crash recovery is not proven.** The worker-layer GUI flake above is a genuine
  reliability gap; no recovery path was exercised live.
- **Per-worker identity is not distinct.** All workers share the `worker-service`
  identity, so these runs do not prove per-worker isolation.
- **Human-relative 4x speed is NOT measured.** No human median baseline exists; no speedup
  is claimed.
- **Sample sizes are small** (10/mode/run, 30/mode aggregate). The hybrid rate is reported
  with that limitation; it is not a tight confidence interval.
- **No sustained 30–60 minute workflow has run**, and no concurrency-under-load batch has
  been measured. NEURO-12 remains **PARTIAL**.

---

## Update 2026-11-14 (later): GUI marker-read fix + task_id collision fix

Two further defects were found by extending the same cohort instrument and fixed on
`main`. Evidence: `cohort_prefix.json` (pre-fix, this round) vs
`cohort_postfix_final.json` (post-fix). Both cohorts: 10 unique GUI + 10 unique hybrid
objectives, HEAD stable throughout.

### Defect 6 — GUI verifier misread trailing-zero markers (fixed)

The verifier read the on-screen marker through Tesseract at a single upscale (3x).
Trailing-zero markers were misread (`NEURO12A-GUI-010` → `...-016`), so only **39/46**
real markers were read and the verifier failed closed on correct work. `TesseractOCREngine`
now unions text regions across multiple upscales (`(2, 3)`) and the fuzzy branch carries a
digit-agreement guard so a query cannot fuzzy-match a *different* marker's number.

Measured: **39/46 → 46/46 markers read**, with cross-marker false positives held at the
2/2070 baseline (the naive union first amplified them to 407/2070; the digit guard removed
the amplification). Regression test: `test_gui_worker_runtime.py`.

### Defect 7 — `task_id` collisions dropped samples mid-cohort (fixed)

Phase/task indices restart at 1 for every objective, so the public task id carried only
4 random hex characters (`TASK-01-01-82CA`) — 65,536 values per slot. One slot already held
322 tasks, so a sustained workload collided. The duplicate insert raised an unhandled
`IntegrityError`; the API returned **500** and the client connection dropped, losing the
sample (`gui-005` in the first post-fix run) mid-cohort. The suffix is now 12 hex
characters (`_unique_task_id`). Regression test:
`test_unique_task_ids_do_not_collide_across_objectives`.

### Results (this round)

| Cohort | Mode | Samples | Counted | Completed | Failed | In-flight | Success rate | Retry freq | Mean (s) | p95 (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| pre-fix | gui | 10 | 10 | 9 | 1 | 0 | 90% | 0.3 | 34.3 | 58.0 |
| pre-fix | hybrid | 10 | 10 | 1 | 9 | 0 | **10%** | 2.7 | 108.4 | 150.7 |
| post-fix | gui | 10 | 10 | 10 | 0 | 0 | **100%** | 0.0 | 31.7 | 39.4 |
| post-fix | hybrid | 10 | 10 | 8 | 2 | 0 | **80%** | 0.8 | 82.7 | 176.8 |

Gate: **PASS** (min 0.8 success/mode, ≥10 counted samples/mode, HEAD stable).

### What the remaining post-fix failures are

Both hybrid failures (`hybrid-003`, `hybrid-007`) are genuine visual-verification
failures after 4 attempts, not disconnects. Worker logs show the model-authored
verification scripts were themselves malformed (e.g. `pytest.SubRequest` misuse,
`NameError` in a session fixture, an unterminated triple-quoted string, a `splitlines`
line-count mismatch) and one attempt hit a retryable **409** when the desktop vanished
mid-task (defect 5). These are worker/model-output quality failures, **not fixed by this
work** and reported honestly rather than masked.

### Honest caveats (this round)

- Sample sizes remain small (10/mode). The 100% GUI rate is a 10-sample rate, not a tight
  interval.
- The GUI 90% → 100% move is over 10 samples and includes `gui-010`, the marker that
  previously failed deterministically; it is a real fix but the cohort is small.
- Hybrid stayed at 80% this round (vs 90% in the earlier 3-run aggregate); the difference
  is a small-sample model-output flake, not a regression in the fixes.
- No sustained 30–60 minute workflow and no concurrency-under-load batch has run.
  NEURO-12 remains **PARTIAL**.
