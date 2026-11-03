# ANCOS NEURO-0 system audit

**Audit snapshot:** 2026-11-03
**Latest reviewed sources:** `81391f4`; local dashboard edits remain user-owned and untouched.

**Repository:** local `neural-company` workspace
**Acceptance:** NEURO-0 evidence baseline recorded; production end-to-end acceptance is **not met**. Current-source workstation lifecycle evidence is recorded in the follow-up; it does not change this verdict.

## Scope and method

This is a repository and local-environment audit, not a claim that ANCOS is operating as an autonomous company. It combines source/document inspection, the full Python test suite, Ruff, and a dashboard production build. A passing unit or component test is evidence for that component contract only; it is not evidence of a live employee completing work.

The current dashboard edits and pre-existing removed screenshot assets were preserved. Tests were explicitly requested in the NEURO brief. Python test support packages were installed into the ignored local virtual environment only; tracked dependency manifests were not changed.

## Findings

1. ANCOS has substantial implemented subsystems: Boss planning, dynamic employee records, task/DAG APIs, model routing, GUI perception/action/recovery/verification modules, resource leases, retrieval/memory, governance, meetings, evolution, backup, and audit modules.
2. The strongest acceptance gap is still the live execution chain. One controlled current-source terminal worker-to-independent-verifier protocol receipt now exists, but it used a manually seeded task, fixed local planner stub, and local harness rather than the long-running Compose profiles. A live GUI action adapter and successful owner objective → recruitment → task execution → independent verification receipt remain unproven.
3. Owner-authenticated objective/staffing and owner-only project reporting now have focused API/DB tests, with separate owner/Boss audit actors. This does not prove a live mission, complete recruitment lifecycle, or employee reuse after session teardown. Direct employee creation and legacy bootstrap authority still need release-level policy review.
4. The live company view is code-rendered and maps backend records, but polls APIs. Desk placement is local role-based placement rather than persisted workstation assignment; persisted meeting speaker activity is not wired. The backend was unavailable at the initial audit snapshot; the current-source validation follow-up below records the isolated runtime result.
5. Resource scheduler/profile mismatch was due to a hardcoded one-slot cap overriding declared 4/8/16 hardware profiles. The cap is now an explicit lowerable deployment setting (`ANCOS_MAX_ACTIVE_DESKTOPS`, Compose default 1); mocked scheduler/integration tests pass. No live multi-session resource capacity has been measured. Container tests still require a configured restricted broker; Docker Linux was unavailable at the initial audit snapshot.
6. Laya is intentionally optional and checkpoint integrity has been documented, but real model inference was not demonstrated in this runtime. Optional/offline fallback should remain clearly distinguished from a successful neural inference.

## Environment snapshot at initial audit time (historical; current follow-up below)

| Check | Result | Limits |
|---|---|---|
| Python suite | Latest full run: 417 collected; 405 passed, 12 skipped, zero failed; 5 warnings | P0 live broker provisioning skips when broker credentials are absent; optional model/runtime tests also skip. Workstation/reaper API tests use fakes; no live Docker proof. See `NEURO_TEST_EVIDENCE.md`. |
| Ruff | Pass | `uv run --no-sync ruff check services tests` passes after fixing six findings. |
| Dashboard production build | See `NEURO_TEST_EVIDENCE.md` | Build runs Next.js compile, lint/type validation, and static generation. |
| Python mypy | See `NEURO_TEST_EVIDENCE.md` | A run was attempted; only claim a result if a completed process result is captured there. |
| Docker engine | Unavailable | Docker Desktop Linux engine named pipe was absent. No broker-backed execution can be verified locally. |
| Core API | Unavailable on ports 12000/12003 | No live employee/task workflow or API-backed browser acceptance in this environment. |
| Existing services | Ports 12001 and 12002 occupied | Their owning processes/workspaces are not ANCOS services; left untouched. |
| Performance | No valid baseline | No reproducible live API or worker load test was run; do not infer latency/capacity from unit tests. |

## Acceptance verdict

NEURO-0 records a local baseline and prioritizes the gaps. It does **not** accept the system as production-ready or autonomous. The capability labels and evidence hierarchy are in `NEURO_CAPABILITY_MATRIX.md`; the master design is in `NEURO_ARCHITECTURE.md`; remediation order and the complete user-provided NEURO-0–13 sequence are in `NEURO_PHASE_PLAN.md`.

## NEURO-1 follow-up (2026-11-02)

The control-plane slice now requires an owner identity on `POST /boss/objective`, invokes Boss staffing when verified skills or idle capacity are insufficient, records separate owner-submission and Boss-staffing audit events, and disables direct employee creation. The fixed-name bootstrap helper now generates and reuses dynamic profiles. The owner-only project report reads actual task, attempt, and verifier evidence. Five focused tests passed, including production-mode credential rejection, recruitment, and report accuracy. At the time of this follow-up, the live task consumer and verifier were unavailable. A later controlled worker/verifier receipt is recorded below; neither result satisfies the full NEURO-1 end-to-end mission.

## Current repository delta (2026-11-03)

- **NEURO-1:** The dated follow-up above supersedes the initial audit-time recruitment finding; current objective-route staffing is owner-authenticated and focused-tested. No live objective-to-verified-report mission exists.
- **NEURO-2/3 at the initial audit snapshot:** `455f752` adds an opt-in terminal-only worker, supported-mode claim filter, start/heartbeat/submit/fail protocol, lease-checked Core API command endpoint, and restricted-broker execution. `395f53d` fixes verifier rejection lifecycle: preserve the rejected receipt, consume bounded retry budget with persisted backoff, increment fencing, and fail terminally on exhaustion. At that earlier snapshot the verifier replayed declared pytest/npm-test commands and recorded a bounded path/size/SHA-256 workspace snapshot through an attempt-fenced endpoint. The snapshot is verification-time metadata only, not a task-start diff or correctness review. The then-latest focused regression passed (36 tests); the Linux Docker engine was unavailable, so no live consumer receipt existed at that time. The current live protocol receipt and later verification results are recorded below.
- **NEURO-4:** Hardware profile ceilings, deployment caps, and Compose limits agree. The scheduler supports DB-backed per-host slot leases and fencing. Three sequential current-source production-mode live cycles with distinct owner/approver credentials proved approved create/read/capture/terminate, workstation isolation, lease release, and audit-chain integrity. One live desktop survived an isolated API container restart and was recovered from DB/broker state. A separate planned broker stop returned HTTP 503 (`runtime_unavailable`) with the lease still active; broker restart restored GET and 1280×800 capture, followed by approved termination and valid audit verification. The patched source was rebuilt and the 503 behavior plus recovery were live-verified. A one-session 1 CPU / 1 GiB idle sample peaked at 147.4 MiB and 0.58% CPU. Reuse validation exposed duplicate deterministic container rows and ambiguous old lease lookup; both are fixed and covered by regression tests. Concurrent task load, multi-host, abrupt crash recovery, and live task checkpoint resumption remain unverified.
- **NEURO-4 update:** Temporary current-source API/broker services were isolated from and removed without replacing the user stack. The approved validation employee and SQLite copy remain isolated under `data/neuro-validation-current`; the employee workspace volume persists by policy.
- **NEURO-2/3 live protocol update:** A controlled manually seeded task completed through the current-source terminal worker claim/start/execute/submit path and an independent verifier replay. The verifier used its own credential and a disposable network-disabled, read-only sandbox; `python3 -m unittest -v` passed and a nonempty one-file hash manifest was recorded. The current desktop image lacked pytest. The task completed, employee returned IDLE, audit chain validated (51 records), and approved cleanup released the desktop lease. This does not prove Boss objective/provider planning, long-running worker/verifier profile operation, lifecycle failure paths, or NEURO-1/3 acceptance.
- **Worker claim/replay fix:** Idle employees with approved running desktops can be conditionally transitioned to WORKING for a single active task; a second task cannot be claimed concurrently. Python stdlib unittest is now an allowed verifier replay command. Focused regression: 12 passed.
- **Verified DAG continuation:** The worker claim path dispatches one unassigned, pending DAG task only when all dependencies are completed with persisted passing verifier receipts, assigning it conditionally to an idle employee with an already-running desktop. Tests verify dependent work advances after independent approval and cannot advance from a legacy completed row without proof. This solves the code-path dead end after the initial assigned task; no live multi-step objective receipt exists yet.
- **Audit chain ordering:** Consecutive audit writes now advance the stored timestamp by one microsecond when needed, so timestamp ties cannot make the append query link a new record to an earlier signature. Sequential SQLite coverage passes; concurrent multi-process audit-writer serialization remains unverified.
- **Roadmap fidelity:** The phase plan/status now preserve the user's full NEURO-0–NEURO-13 objectives. This corrects the prior shortened 0–9 plan, which did not represent the complete master mission.
- **Validation freshness:** Latest complete full suite: 426 collected; 414 passed, 12 skipped, zero failed, 5 warnings (44.27 seconds). Repo-wide Ruff on `services tests` passes. Docker Desktop 29.8.2 is available. Existing user API, broker, and Redis remained running; isolated validation services were removed. Single-session lifecycle/recovery and one controlled task worker/verifier protocol receipt are recorded; verified DAG continuation has regression coverage; checkpoint integrity and payload handoff are locally tested. None proves task-load capacity, live multi-step task progression, live task resumption, abrupt API/broker failure recovery, long-running consumer health, or full mission acceptance.
