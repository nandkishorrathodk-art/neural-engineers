# ANCOS NEURO test and build evidence

**Baseline date:** 2026-11-02

**Environment:** Windows local workspace; Python 3.12.14; pytest 9.2; Next.js 15.5.26.

## Latest update — 2026-11-03

- Resource capacity mismatch fix: `HardwareProfiler` now uses profile ceilings (4/8/16) unless a lower `ANCOS_MAX_ACTIVE_DESKTOPS` ceiling is configured; core Compose keeps a one-slot default until operators opt in to more. `DesktopSlotPool` honors its configured slot count. Unit and cognitive integration scheduling tests use a fake container provisioner so they verify queue/lease/checkpoint policy without pretending Docker is available. `uv run --no-sync pytest tests/unit/test_resource_scheduler.py tests/integration/test_cognitive_fabric_integration.py -q --tb=short` — **18 passed**; Ruff passes on changed scheduler/test files.
- Full regression after integration-contract and timing fixes: `uv run --no-sync pytest -q` — **405 collected; 393 passed, 12 skipped, zero failed, 5 warnings** (117.52 seconds). One P0 live restricted-broker provisioning test skips when broker URL/token are absent; optional model/runtime skips are not counted as passes. Ruff: `uv run --no-sync ruff check services tests` — **All checks passed**.
- Focused Phase 6 approval/master-benchmark, Phase 7 GUI capture, RAG elapsed-time, and P0 broker-availability regression: **4 passed, 1 skipped**. The benchmark uses an explicit broker test double for provisioning orchestration; the P0 test remains live when credentials are configured. GUI endpoint coverage injects an authenticated workstation test double. The approval test injects approver identity through the auth dependency, never from the request body.
- Agentic RAG elapsed-time reporting uses a monotonic high-resolution clock for timeout checks and duration measurement, avoiding wall-clock jumps and measurements rounded down to zero.
- Durable desktop lease regression: `uv run --no-sync pytest tests/integration/test_resource_scheduler_persistent_leases.py tests/unit/test_resource_scheduler.py tests/integration/test_cognitive_fabric_integration.py -q` — **21 passed**. SQLite-backed cases prove competing schedulers cannot claim the same host slot, release advances the fencing token, provisioning failures release their reservation, and unconfirmed container termination keeps the lease and slot occupied. Profile CPU/memory limits are asserted at the provisioning boundary. Ruff passes for the scheduler and these tests. The scoped mypy launcher was blocked by Windows Application Control (error 4551), and `python -m mypy` is unavailable in the workspace; no mypy result is claimed for this change.
- Full suite after durable lease and profile-limit integration: `uv run --no-sync pytest -q` — **408 collected; 396 passed, 12 skipped, zero failed, 5 warnings** (129.05 seconds). The final pool-ID namespace assertion and Compose environment rendering were then verified by the 21-test focused scheduler run, full Ruff, and `docker compose config --no-interpolate`.
- Capacity boundary: Docker Compose parsing confirms the core service receives `ANCOS_MAX_ACTIVE_DESKTOPS=${ANCOS_MAX_ACTIVE_DESKTOPS:-1}`. This proves configuration rendering, not a live session limit. Docker Desktop's Linux engine is unavailable, and there is no multi-session resource benchmark or durable scheduler lease integration.
- Added a broker-mediated verifier workspace snapshot: at most 2,000 regular files, 10 MiB per file, 100 MiB total, and 2,048 characters per path; `.git`, `.venv`, `node_modules`, `__pycache__`, and `.pytest_cache` are excluded. It records paths, byte sizes, and SHA-256 only, from a disposable network-disabled container with a read-only mount. Each file is checked for mutation while hashing. This is a verification-time snapshot, not a task-start diff, file-content review, or proof of correctness. The terminal verifier fails closed when the manifest is absent, empty, invalid, or incomplete.
- Manifest/verifier regression: `uv run --no-sync pytest tests/unit/test_verifier_worker.py tests/unit/test_neuro_phase1_authority.py tests/unit/test_terminal_worker.py tests/unit/test_verification.py -q` — **36 passed**. The test set includes mocked broker isolation, valid and incomplete manifests, and stale fencing. Docker is still unavailable, so manifest collection has not run in a live container.
- Added an opt-in terminal-only consumer and bounded verifier-rejection recovery. If an independent review rejects an attempt, the attempt retains the rejection receipt, the task is requeued with persisted backoff and a new fencing token, and retry exhaustion moves the task to terminal `FAILED`. Stale verifier decisions cannot attach to the next attempt.
- Focused regression run: `uv run --no-sync pytest tests/unit/test_neuro_phase1_authority.py tests/unit/test_terminal_worker.py tests/unit/test_verification.py` — **23 passed**. Ruff passed on the changed repository, verifier route, and tests; scoped mypy passed on verifier route and repository. Earlier terminal worker validation also passed 23 tests, 2 skipped, Ruff, mypy, and Compose profile rendering.
- Docker Linux engine is unavailable in this environment. No worker image build, verifier consumer runtime, or live end-to-end receipt was produced. NEURO-2 and NEURO-3 remain in progress; NEURO-5 remains not accepted.
- Follow-up verifier consumer validation (source prepared after `1e6f897`): focused run **33 passed** across verifier-worker, terminal-worker, authority/recovery, verifier authorization, and verifier suites; Ruff and scoped mypy passed; `docker compose --profile workers --profile verifiers config --no-interpolate` parsed both isolated profiles. The broker test asserts a disposable network-disabled container, read-only workspace mount, no capabilities, timeout, and cleanup. The consumer only independently replays declared pytest/npm-test commands for a small exact allowlist of test-only acceptance criteria; it does not inspect arbitrary semantics or collect artifacts, and Docker runtime is still unavailable.

## Python tests

Command: `uv run --no-sync pytest`
NEURO-0 baseline result: **376 collected; 334 passed, 31 failed, 11 skipped; 5 warnings** (187.69 seconds).

NEURO-0 baseline failure clusters (historic, before verifier and capacity changes):

- Restricted Docker broker provisioning unavailable (including cognitive fabric desktop sessions and P0 zero-trust/container manager tests).
- Phase 2 parallel DAG / LLM fallback contracts.
- Phase 5 methods and meta-learning API contracts.
- Phase 6 API security/benchmark contracts.
- Phase 7 GUI workflow contract.
- Cognitive-fabric skill/evolution loops.
- Ten resource scheduler expectations conflicted with one-slot capacity / unavailable broker assumptions; current scheduler logic tests pass under deterministic container mocks. Runtime resource measurement remains absent.

Laya runtime-dependent cases were skipped because the optional inference runtime/weights were not available to this run. Skips are not passes. Five warnings include Python tar extraction deprecations in backup code/tests.

The first test invocation failed at collection because the local environment lacked `alembic`; declared dev test packages were installed only in the ignored `.venv`, and the full run was retried. No tracked source or dependency manifests were changed for test setup.

## Current NEURO-1 / NEURO-2 execution-protocol slices

- Latest focused authority, staffing, report, and API run before execution-protocol additions: **5 passed**. The production API test rejects absent/worker credentials, accepts the owner token, verifies separate owner/Boss audit actors, and confirms a dynamically recruited roster. The report tests cover owner-only access, passing verifier evidence, unverified completion, and failed attempt details. An unverified declared skill remains a staffing gap; it closes only after the test marks it verified.
- Owner-facing project report tests verify owner-only access, actual pending/assigned counts, attempt failures, and the rule that only a passing stored verifier receipt contributes to `verified`.
- Task plans now persist an enumerated execution mode (`gui`, `terminal`, `hybrid`, `authorized_tools`) through task storage, worker claim payload, and task APIs. A migration adds a legacy-safe `terminal` default; the approval and task-mode migration branches have a single merge head. The complete Alembic chain upgraded a disposable SQLite database successfully.
- Worker failure protocol now supports bounded retry for classified retryable errors, persists exponential backoff, increments fencing tokens, and returns exhausted/nonretryable work to terminal failure. Expired worker leases also consume the retry budget; focused coverage confirms retry backoff prevents immediate reclaim. This is repository/API behavior, not a deployed worker consumer.
- Focused authority/recovery run after the lease-exhaustion test commit `4dde8be`: **7 passed**. Follow-ups add owner-report retry visibility, close active attempts on authorized cancellation, filter claims by supported execution mode, and add an opt-in terminal worker. Latest worker loop + lease-checked command route + adjacent lifecycle/planner API run: **23 passed, 2 skipped**. Changed-file Ruff and scoped mypy passed. `docker compose --profile workers config --no-interpolate` parsed the profile and showed the worker has no broker token, bounded CPU/memory/PIDs, read-only root, and dropped capabilities. Docker daemon is unavailable, so no image build or runtime test was done. Focused planner/API/recruitment/report plus authority tests before that test-only commit: **17 passed, 2 skipped**.
- Historical full regression at source commit `c979cb2`: **382 collected; 339 passed, 32 failed, 11 skipped; 5 warnings**. The current full result is recorded above. Failure counts vary because at least one retrieval/integration contract is nondeterministic; do not infer causal fixes from aggregate counts alone.
- Full Ruff check on 2026-11-03: `uv run --no-sync ruff check services tests` — **passed** after fixing six findings (five import-order issues, one unused import).
- Scoped mypy (`--follow-imports=skip --disable-error-code=import-untyped`) passed on the four changed Python source modules; imported dependencies were intentionally not checked by this scoped command. Mypy is not green for the repository (full NEURO-0 baseline: 208 errors in 37 files).

## Static checks and builds

| Check | Result |
|---|---|
| `uv run --no-sync ruff check services tests` | **Passed** after automatically fixing six findings: five import-order issues and one unused import. |
| Python mypy | **Failed**: 208 errors in 37 files (237 source files checked). Findings include missing third-party stubs/modules (Alembic, Docker, NetworkX, psutil), nullable-value/type narrowing issues, mismatched model/API arguments, GUI action requests missing required fields, and resource/profile typing inconsistencies. |
| Dashboard `npm run build` | **Passed**. Next.js 15.5.26 compiled, completed TypeScript validity checking, generated all 9 static pages, and finalized build traces. |
| Dashboard lint script | Not run; package script uses `next lint`, which is not an independent backend quality signal. Ruff result above is the Python lint baseline. |

## NEURO-0 runtime and performance snapshot (2026-11-02)

- Docker Desktop Linux engine was unavailable (Docker engine named pipe absent).
- No core API listener was found on ports 12000 or 12003. Ports 12001 and 12002 were already occupied by separate processes and were not changed.
- At this snapshot, Compose defined Redis, restricted Docker broker, core API, and dashboard only. The later `455f752` change adds an opt-in terminal worker; no verifier consumer has been added.
- Docker lease, retry, backoff, and fencing logic has focused in-process DB coverage, but no deployed consumer or live crash-recovery receipt exists.
- No real task, live GUI action, independent verifier receipt, throughput measurement, latency percentile, or cost benchmark was captured. This remains true through the latest focused checks.
- Therefore P50/P95/P99, queue delay, throughput, failure recovery, and cost per verified task are **NOT MEASURED**. Do not use synthetic tests as a performance baseline.

## Repeat commands

```powershell
uv run --no-sync pytest
uv run ruff check services tests
uv run mypy services/core/neural
cd apps/dashboard; npm run build
```

Record command, commit, OS/runtime, configuration (redacting secrets), exit code, duration, test counts, and relevant artifact hashes for each future baseline.
