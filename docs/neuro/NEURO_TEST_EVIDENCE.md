# ANCOS NEURO test and build evidence

**Baseline date:** 2026-11-02

**Environment:** Windows local workspace; Python 3.12.14; pytest 9.2; Next.js 15.5.26.

## Latest update — 2026-11-03

- Added an opt-in terminal-only consumer and bounded verifier-rejection recovery. If an independent review rejects an attempt, the attempt retains the rejection receipt, the task is requeued with persisted backoff and a new fencing token, and retry exhaustion moves the task to terminal `FAILED`. Stale verifier decisions cannot attach to the next attempt.
- Focused regression run: `uv run --no-sync pytest tests/unit/test_neuro_phase1_authority.py tests/unit/test_terminal_worker.py tests/unit/test_verification.py` — **23 passed**. Ruff passed on the changed repository, verifier route, and tests; scoped mypy passed on verifier route and repository. Earlier terminal worker validation also passed 23 tests, 2 skipped, Ruff, mypy, and Compose profile rendering.
- Docker Linux engine is unavailable in this environment. No worker image build, verifier consumer runtime, or live end-to-end receipt was produced. NEURO-2 and NEURO-3 remain in progress; NEURO-5 remains not accepted.

## Python tests

Command: `uv run --no-sync pytest`
NEURO-0 baseline result: **376 collected; 334 passed, 31 failed, 11 skipped; 5 warnings** (187.69 seconds).

Failure clusters:

- Restricted Docker broker provisioning unavailable (including cognitive fabric desktop sessions and P0 zero-trust/container manager tests).
- Phase 2 parallel DAG / LLM fallback contracts.
- Phase 5 methods and meta-learning API contracts.
- Phase 6 API security/benchmark contracts.
- Phase 7 GUI workflow contract.
- Cognitive-fabric skill/evolution loops.
- Ten resource scheduler expectations that conflict with one-slot local capacity / unavailable broker assumptions.

Laya runtime-dependent cases were skipped because the optional inference runtime/weights were not available to this run. Skips are not passes. Five warnings include Python tar extraction deprecations in backup code/tests.

The first test invocation failed at collection because the local environment lacked `alembic`; declared dev test packages were installed only in the ignored `.venv`, and the full run was retried. No tracked source or dependency manifests were changed for test setup.

## Current NEURO-1 / NEURO-2 execution-protocol slices

- Latest focused authority, staffing, report, and API run before execution-protocol additions: **5 passed**. The production API test rejects absent/worker credentials, accepts the owner token, verifies separate owner/Boss audit actors, and confirms a dynamically recruited roster. The report tests cover owner-only access, passing verifier evidence, unverified completion, and failed attempt details. An unverified declared skill remains a staffing gap; it closes only after the test marks it verified.
- Owner-facing project report tests verify owner-only access, actual pending/assigned counts, attempt failures, and the rule that only a passing stored verifier receipt contributes to `verified`.
- Task plans now persist an enumerated execution mode (`gui`, `terminal`, `hybrid`, `authorized_tools`) through task storage, worker claim payload, and task APIs. A migration adds a legacy-safe `terminal` default; the approval and task-mode migration branches have a single merge head. The complete Alembic chain upgraded a disposable SQLite database successfully.
- Worker failure protocol now supports bounded retry for classified retryable errors, persists exponential backoff, increments fencing tokens, and returns exhausted/nonretryable work to terminal failure. Expired worker leases also consume the retry budget; focused coverage confirms retry backoff prevents immediate reclaim. This is repository/API behavior, not a deployed worker consumer.
- Focused authority/recovery run after the lease-exhaustion test commit `4dde8be`: **7 passed**. Follow-ups add owner-report retry visibility, close active attempts on authorized cancellation, filter claims by supported execution mode, and add an opt-in terminal worker. Latest worker loop + lease-checked command route + adjacent lifecycle/planner API run: **23 passed, 2 skipped**. Changed-file Ruff and scoped mypy passed. `docker compose --profile workers config --no-interpolate` parsed the profile and showed the worker has no broker token, bounded CPU/memory/PIDs, read-only root, and dropped capabilities. Docker daemon is unavailable, so no image build or runtime test was done. Focused planner/API/recruitment/report plus authority tests before that test-only commit: **17 passed, 2 skipped**.
- Latest full regression was run before `4dde8be`, at source commit `c979cb2`: **382 collected; 339 passed, 32 failed, 11 skipped; 5 warnings** (5,424.76 seconds). The focused run after `4dde8be` covers its added test; the full suite has not been rerun at that commit. This is one additional failure versus NEURO-0 (31); do not claim the baseline failure count is unchanged. Failure clusters remain in Docker, phase integrations, cognitive loops, methods/meta-learning, and resource scheduler. Agentic RAG status varied across runs.
- Full Ruff baseline remains 7 findings outside the changed files.
- Scoped mypy (`--follow-imports=skip --disable-error-code=import-untyped`) passed on the four changed Python source modules; imported dependencies were intentionally not checked by this scoped command. Mypy is not green for the repository (full NEURO-0 baseline: 208 errors in 37 files).

## Static checks and builds

| Check | Result |
|---|---|
| `uv run ruff check services tests` | Failed with 7 findings: import ordering in `services/core/neural/api/routes/gui_intelligence.py`, `services/core/neural/containers/manager.py`, `tests/integration/test_live_screen_capture_api.py`, `tests/unit/test_container_screenshot.py`, `tests/unit/test_laya_adapter_integrity.py`, `tests/unit/test_phase2_tasks_dag.py`; unused `ScreenCaptureEngine` import in GUI API route. |
| Python mypy | **Failed**: 208 errors in 37 files (237 source files checked). Findings include missing third-party stubs/modules (Alembic, Docker, NetworkX, psutil), nullable-value/type narrowing issues, mismatched model/API arguments, GUI action requests missing required fields, and resource/profile typing inconsistencies. |
| Dashboard `npm run build` | **Passed**. Next.js 15.5.26 compiled, completed TypeScript validity checking, generated all 9 static pages, and finalized build traces. |
| Dashboard lint script | Not run; package script uses `next lint`, which is not an independent backend quality signal. Ruff result above is the Python lint baseline. |

## Runtime and performance baseline

- Docker Desktop Linux engine was unavailable (Docker engine named pipe absent).
- No core API listener was found on ports 12000 or 12003. Ports 12001 and 12002 were already occupied by separate processes and were not changed.
- Compose defines Redis, restricted Docker broker, core API, and dashboard only; it does not define a task worker or verifier consumer.
- Docker lease, retry, backoff, and fencing logic has focused in-process DB coverage, but no deployed consumer or live crash-recovery receipt exists.
- No real task, live GUI action, independent verifier receipt, throughput measurement, latency percentile, or cost benchmark was captured.
- Therefore P50/P95/P99, queue delay, throughput, failure recovery, and cost per verified task are **NOT MEASURED**. Do not use synthetic tests as a performance baseline.

## Repeat commands

```powershell
uv run --no-sync pytest
uv run ruff check services tests
uv run mypy services/core/neural
cd apps/dashboard; npm run build
```

Record command, commit, OS/runtime, configuration (redacting secrets), exit code, duration, test counts, and relevant artifact hashes for each future baseline.
