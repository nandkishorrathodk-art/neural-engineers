# NEURO-12D GUI save reliability report (false-success elimination)

Generated: 2026-11-14 (UTC). Evidence in
`docs/neuro/evidence/neuro12d_gui_save_reliability_2026-11-14/`.

## Symptom

A hybrid objective (terminal prepare → GUI edit → terminal verify) intermittently
loses the GUI edit: every one of the 13 GUI steps reports `gui.status=success`
and `steps_executed=13`, yet `handoff.txt` is unchanged on disk and the
independent verifier fails. The GUI layer reported a **false success**.

## Root cause

The GUI plan's final step was a bare `KEY_HOTKEY ctrl+s`. Sending `Ctrl+S` proves
a keystroke was dispatched, not that the file was written. Two mechanisms make the
save silently vanish while the loop still reports success:

1. **Lost focus before the edit.** The location-bar `ctrl+l` had no settle before
   typing, so if focus slipped the edit landed in an `Untitled` buffer; `Ctrl+S`
   then opened a **Save As** dialog and the edit was never persisted.
2. **Dropped save keystroke.** Even with the right document focused, a save event
   can be dropped (focus hand-off, lagging event queue). The loop verified only a
   pixel delta and an optional `expected_text` — neither is a save postcondition —
   so the step still reported `SUCCESS`.

Reproduction of mechanism 1 in a disposable container produced `Untitled 1 -
Mousepad`, `saved=0` at every timing gap (0/0.05/0.1/0.3/0.6 s). The failed real
run's receipts show all 13 steps `success` with the file unchanged, and the verify
phase never writes `handoff.txt` (its commands only read the file), so the loss is
strictly in the GUI save.

## Fix: a save postcondition from the application's own state

A GUI-native, non-pixel signal already exists: text editors prefix a *modified*
document's title with `*` and drop it once the file is written
(`*~/workspace/handoff.txt - Mousepad` → `~/workspace/handoff.txt - Mousepad`).
The fix turns "I sent Ctrl+S" into "the app reports the file saved".

New `CONFIRM_SAVED` action type:

- `ContainerDesktopDriver.window_titles()` reads visible top-level window titles
  over the display (fail-soft: an empty display returns `[]`, not an error).
- `GUIExecutionLoop` gains a `window_state_provider`; on `CONFIRM_SAVED` it polls
  the editor title until the document named in `expected_text` is present **without**
  the `*` modified marker, or the settle deadline (default 4000 ms) expires.
- **Self-heal:** if the right document is open but still dirty, the loop re-issues
  `Ctrl+S` (at most twice) before failing — a dropped keystroke recovers instead of
  failing the whole task. Saving an already-saved file is a no-op, so the retry is
  harmless.
- **Fail closed:** with no matching unmodified document window the step raises
  `GUIActionBlockedError`, so a lost edit fails **at the save**, not silently later.
- `derive_document_edit_steps` now appends a `CONFIRM_SAVED` step (keyed on the
  document file name) after the `Ctrl+S`, so every document-edit plan carries the
  postcondition.
- Wired through `GuiStepPlan`/`GuiWorkerEngine` and the API schema
  (`WorkerGuiStep`, `GUIActionRequest`), so the step survives the worker → core-api
  hop.

## Files changed

| File | Change |
|---|---|
| `services/core/neural/gui_intelligence/models.py` | `CONFIRM_SAVED` action type; `expected_text` field |
| `services/core/neural/gui_intelligence/runtime/desktop_driver.py` | `window_titles()` window-state read |
| `services/core/neural/gui_intelligence/action/execution_loop.py` | `window_state_provider`, `_document_saved`, `CONFIRM_SAVED` handling + self-heal |
| `services/core/neural/gui_intelligence/runtime/planner.py` | plan emits the `CONFIRM_SAVED` postcondition |
| `services/core/neural/gui_intelligence/worker.py` | `GuiStepPlan.expected_text`, provider wiring, payload pass-through |
| `services/core/neural/api/schemas/requests.py` | `WorkerGuiStep` accepts `CONFIRM_SAVED` + `expected_text` |
| `tests/unit/test_gui_worker_runtime.py` | regression tests (below) |

## Regression tests

- `test_planner_confirms_the_save_from_app_state_after_ctrl_s` — plan ends with a
  `CONFIRM_SAVED` keyed on the file name, after the `Ctrl+S`.
- `test_confirm_saved_succeeds_when_document_is_unmodified` — clean title passes.
- `test_confirm_saved_fails_closed_when_document_is_still_dirty` — `*`-prefixed
  title fails the step (`BLOCKED_BY_SAFETY`).
- `test_confirm_saved_reissues_ctrl_s_when_document_remains_dirty` — a dropped save
  is retried and then succeeds.
- `test_confirm_saved_requires_a_window_state_provider` — fails closed without the
  provider.
- `test_document_saved_ignores_dirty_marker_and_foreign_documents` — the marker
  heuristic rejects a dirty title, a foreign document, and a bare app window.
- `test_driver_window_titles_reads_visible_titles` — driver reads titles over X.

## Live evidence

Clean-room disposable container (`ancos-desktop-anc-emp-repro0001`, Xfce + Mousepad,
`:1` 1280×800), driving the real GUI plan through `GuiWorkerEngine`:

- **Happy path (10 iterations):** `10/10 persisted and verified` — every run
  `status=success`, `saved=True` with `CONFIRM_SAVED` as the final step
  (`live_hybrid_loop_10x.txt`).
- **Lost-save path:** with the `Ctrl+S` step removed the plan reports
  `status=failed`, last receipt `BLOCKED_BY_SAFETY`, error
  `"Save not confirmed: no editor window reported the document as saved within 4000 ms
  (expected 'handoff.txt'); the edit was not persisted."` while the live title stays
  `*~/workspace/handoff.txt - Mousepad` (`live_lost_save_detection.txt`).

The lost edit is now a **hard failure at the save**, never a false success.

## Honest limitations

- The confirmation depends on the editor's `*` title convention (Mousepad, gedit,
  pluma). An application that marks modification only inside its canvas would not be
  confirmable this way; `CONFIRM_SAVED` fails closed in that case rather than
  guessing.
- The 10× live sample is a single-host clean-room measurement, not the multi-objective
  NEURO-12A batch; it isolates the save mechanism, not end-to-end task rates.
- Existing worker-layer flakiness reported in NEURO-12A (missing post-action frames)
  is out of scope and unchanged by this work.

## Verification run

- `ruff check` clean on all modified modules.
- Full suite: **974 passed, 11 skipped** (`pytest -q`).
- Commit: see `git log -1 --format=%H` on `main`.
