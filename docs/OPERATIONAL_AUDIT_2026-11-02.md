# ANCOS operational audit — 2026-11-02

This audit records what was inspected and exercised in the local workspace. A passing component test is not evidence that an employee completed a real task.

## Runtime findings

| Capability | Status | Evidence |
| --- | --- | --- |
| NVIDIA gateway | `VERIFIED_PARTIAL` | Live Boss reasoning and coding inference returned NVIDIA responses in the prior runtime session. The API was healthy. |
| Boss planning | `FIXED_PENDING_LIVE_PLAN` | Boss now uses structured NVIDIA planning when `BOSS_LLM_PLANNING_ENABLED=true`; plans validate unique task IDs and backward-only dependencies. Tests use an injected provider, not a billable network call. |
| Boss health reporting | `FIXED` | Removed hardcoded active-mission count and nominal/operational claims. Status exposes configured provider/auth separately and reports the worker as `not_reported`; overall health remains degraded until a worker heartbeat exists. |
| Task worker process | `NOT_IMPLEMENTED` | Compose still has no worker consumer. `/internal/worker` is a protocol only. No task is autonomously consumed. |
| Live GUI execution | `NOT_IMPLEMENTED` | Production screen capture deliberately rejects synthetic frames, and there is no live screenshot/action adapter. The running employee image has Chromium but no `xdotool`, `scrot`, or Mousepad. |
| Independent verification process | `NOT_IMPLEMENTED` | The authenticated verifier queue/review API exists, but Compose has no verifier consumer. |
| Completion gate | `BUILT` | Repository completion remains gated on a passing independent verification receipt. |
| Existing task data | `STALE_TEST_DATA` | The running database has `T-COMPLETED-01` titled `T1`, objective `Obj`, assigned to the running employee. It is underspecified and should not be auto-executed. |
| Workstation | `RUNNING` | One employee desktop is running. Replacing it would interrupt the visible session and requires the repository's approval flow. |

## Changes made in this remediation pass

- Connected `BossBrain` to the LLM-backed structured planner when explicitly enabled by configuration.
- Added validation for model-produced task plans and explicit `plan_source` metadata for rules fallback.
- Changed Boss status to report actual counts/configuration and an unreported worker runtime instead of fabricated health values.
- Fixed empty DAG execution to succeed without requiring a worker; non-empty DAGs still fail closed if no worker is supplied.
- Prevented test runs from loading provider keys from `.env`; live-cloud calls are not part of ordinary tests.
- Reclassified the old “autonomous acceptance” test as a component contract test because it creates its own artifacts and does not use a live employee.
- Marked Docker lifecycle tests as requiring an explicitly disposable test Docker environment, and aligned one-workstation tests with the current capacity limit.
- Fixed the Next route handler context type and added GUI editor, screenshot, and OCR packages to the employee image. The image was rebuilt; the active desktop still uses its existing container image.
- Stopped logging raw workstation command arguments.
- Added checkpoint SHA-256 verification before the Laya weights are imported, and corrected ordinal score conversion to the official SDK's score contract.
- Corrected the neural benchmark so accuracy, ECE, and Brier calculations compare predictions with fixed labeled examples instead of counting any valid label as correct or treating model output as ground truth.
- Added restricted-broker PNG screenshot capture with payload validation and an owner-authenticated `/gui/screen/capture?employee_id=...` endpoint that refuses to synthesize a frame when the workstation is unavailable.
- Added a preflight check for `scrot` so an older desktop missing GUI tools returns a clear unavailable error instead of passing shell error text into the PNG decoder.
- Kept Laya as an optional runtime integration: removed Laya and PyTorch from project dependencies and the lockfile, restored the standard core Docker install, rebuilt the core image, and verified `importlib.util.find_spec()` returns `None` for both packages in that image.

## Verification

- Dashboard production build: passed after the route handler type fix.
- Focused Python suite: 45 passed, 2 skipped (Boss, phase 1, LLM gateway, verification, scheduler, and checkpoint-integrity components). This ran in an ephemeral Docker test container and did not modify the running services.
- Live screenshot adapter/API contract tests: 4 passed using an injected PNG source; this validates the code path but is not a live VNC capture receipt.
- Dashboard production build: passed on the second run as well.
- Full Python suite after optional Laya checks were correctly skipped: 334 passed, 31 failed, 11 skipped. Phase 2 LLM routing tests now follow the checked-in model routes; scheduler tests use the independent verifier and real test artifact paths; the task API asserts assignment and completion-gate behavior. Remaining failures include live Docker provisioning without a disposable Docker broker, phase 2/5/6/7 contracts, and four-slot capacity tests that conflict with the current one-desktop configuration.
- Laya checkpoint hash matches the pinned SHA-256, but real neural inference is not run in the core container by design. The adapter remains optional and falls back when no separate Laya runtime is installed.
- Employee base image rebuild with GUI tools succeeded; the active desktop has not been restarted or replaced. Updated core API image deployed and health endpoint returned HTTP 200.
- Read-only screenshot request against the existing desktop returned HTTP 503. Broker inspection showed the active older desktop has none of `scrot`, `xwd`, `import`, `ffmpeg`, or `xdotool`; a fresh desktop image is available, but the current GUI session has not been replaced.
- No real employee task has been run or verified in this pass.

## Remaining P0 work

1. Implement and deploy a task consumer with an explicit GUI action adapter and heartbeat reporting.
2. Implement an independently credentialed verifier process that checks artifacts from the workstation, not worker claims alone.
3. Replace the stale `T1` task with a clearly scoped owner-approved task before enabling automatic consumption.
4. Rebuild and replace the current workstation only through the owner approval flow; the running container lacks the GUI tooling added to the new image definition.
5. Run one real task from owner objective through visible GUI interaction, artifact inspection, independent verification, and final delivery.
