# R4 — Multi-step planning, batching, and inference latency

**RESEARCH ONLY.** This workstream corrects the brief's latency premise against the code.

## Research-paper findings

- **OSWorld-Human** (arXiv 2506.16042): "large model calls for planning, reflection, and
  judging account for **most** of the overall latency"; steps can take **3× longer** later
  in a trajectory. The brief's "75–94%" is not in the abstract (see R1) — attribute it to a
  specific figure, don't headline it.
- **HCU** (verified): batching up to 64 actions with **in-call waits and AX diffs** is one
  of the concrete latency levers, alongside prompt-cache stability (a screenshot loop
  invalidates the cache every step because it drops old images).

## What ANCOS actually does (measured from code)

### Production worker GUI path — no LLM per step

- `/internal/worker/tasks/{id}/gui/plan` → `derive_gui_steps` / `derive_document_edit_steps`
  in `runtime/planner.py`. These are **deterministic, regex/rule-based** decomposers. There
  is **no model call** in planning.
- `GuiWorkerEngine` (`gui_intelligence/worker.py`) imports `TesseractOCREngine`,
  `VisualGrounder`, `ContainerDesktopDriver`, `GUIExecutionLoop`, `IndependentVisualVerifier`
  — **no LLM import at all**.
- Per step it: captures a frame, OCRs to ground/verify, dispatches via `xdotool`, sleeps
  for `WAIT_SETTLE` (the document-edit plan uses 2500 + 1200 + 1500 ms = 5.2 s of pure
  waits), and verifies. Latency is OCR + waits + `docker exec` transport.

### Benchmark surface (`/gui/*`) — dual-brain, still no real LLM

- `DualBrainCognitiveController.execute_task` plans once (`System2TaskPlanner.create_plan`,
  rule-based) then loops: `detector.detect` → belief update → `fast_policy.select_action_chunk`
  → `execution_loop.execute_chunk` → `visual_verifier.verify_postcondition`.
- `System2TaskPlanner.llm_gateway` is **stored but never called** (`grep llm_gateway.` →
  empty). So "System 2" is currently rule-based, not model-based.
- The dominant per-step cost is **Tesseract OCR at two upscales** (`_OCR_UPSCALES = (2, 3)`),
  run for grounding *and* again for verification, with per-frame caching keyed on
  `frame_hash`.

### Where the 5–10 s plausibly comes from

OCR (2× tesseract, ~100–500 ms each on 1280×800) + settle waits + per-step verifier OCR +
`docker exec` round-trips for `scrot`/`xdotool`. That can sum to seconds per step **on the
benchmark surface**, but it is **not** "the AI thinking too often" — it is pixel I/O.

## Advantages / limitations of the research directions

| Direction | Applies to ANCOS? | Expected effect | Risk |
|---|---|---|---|
| Compact AX observations / state diffs | Only with the new semantic channel (R2) | Large if AT-SPI works | Contract change |
| Multi-action batching | Partially — `ActionBatcher` exists | Fewer round-trips | Unsafe for destructive actions |
| Targeted-region OCR | **Yes, now** | Direct per-step latency cut | Low |
| Reusable verified skills | Yes (`skills/` exists) | Fewer re-derivations | Low |
| Small fast model for grounding | Research | Replace OCR heuristics | Needs model |
| Replan at state transitions | Yes | Less redundant planning | Medium |
| Observe on uncertainty | **Yes, now** | Fewer screenshots/OCR passes | Medium |

## Hardware / infrastructure requirements

- Targeted-region OCR and observe-on-uncertainty: **none** beyond the current image.
- A semantic channel: AT-SPI packages in the image (R2).

## Expected effect on latency / success

- **Cheapest, safest win:** cache OCR by frame hash (partially present) + OCR only the
  regions that changed (already has `ScreenChangeDetector`) + observe-on-uncertainty. This
  can cut OCR passes per step without touching the contract.
- **Batching** must remain risk-gated: batch confident, non-destructive steps; never batch
  destructive or low-confidence actions.

## Security / verification implications

- Fewer observations must not mean weaker verification: the independent postcondition check
  stays mandatory. Batching must halt on error/timeout/unexpected window change (HCU's own
  rule), and ANCOS should adopt the same fail-fast rule.

## Prototype acceptance criteria

1. Per-step latency is **measured and attributed** (OCR ms, wait ms, transport ms, verify
   ms) on a real run — the prerequisite for any optimisation claim.
2. Targeted-region OCR reduces mean per-step latency with **no** drop in verified success
   rate on the NEURO-12A cohort.
3. A batched chunk halts on the first failed/blocked receipt (no silent continuation).

## Evidence gaps / unresolved questions

- The current 5–10 s/step figure is **not** backed by an ANCOS per-step measurement; the
  NEURO-12A data is per-*task* (~30–60 s). Instrument first.
- How much of per-step time is OCR vs waits vs `docker exec`: **unknown**.
