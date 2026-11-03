# ANCOS NEURO-0 system audit

**Audit snapshot:** 2026-11-02
**Latest reviewed commit:** isolated verifier-consumer slice, 2026-11-03.

**Repository:** local `neural-company` workspace
**Acceptance:** NEURO-0 evidence baseline recorded; production end-to-end acceptance is **not met**.

## Scope and method

This is a repository and local-environment audit, not a claim that ANCOS is operating as an autonomous company. It combines source/document inspection, the full Python test suite, Ruff, and a dashboard production build. A passing unit or component test is evidence for that component contract only; it is not evidence of a live employee completing work.

The current dashboard edits and pre-existing removed screenshot assets were preserved. No source implementation was changed as part of this audit. Tests were explicitly requested in the NEURO brief. Python test support packages were installed into the ignored local virtual environment only; tracked dependency manifests were not changed.

## Findings

1. ANCOS has substantial implemented subsystems: Boss planning, dynamic employee records, task/DAG APIs, model routing, GUI perception/action/recovery/verification modules, resource leases, retrieval/memory, governance, meetings, evolution, backup, and audit modules.
2. The strongest acceptance gap is still the live execution chain. Existing audit evidence says there is no deployed task consumer or verifier consumer in Compose, no live GUI action adapter, and no successful owner objective → recruitment → task execution → independent verification receipt. The task-worker protocol and verifier APIs do not prove a running worker/verifier.
3. A clean-database Boss objective does not recruit by default: `BossBrain.plan_and_create_project()` defaults `auto_recruit_workforce=False`, and the objective route does not enable it. The direct employee creation route and legacy fixed-profile bootstrap helper also need an explicit policy review before claiming Boss-only dynamic recruitment.
4. The live company view is code-rendered and maps backend records, but polls APIs. Desk placement is local role-based placement rather than persisted workstation assignment; persisted meeting speaker activity is not wired. The backend was unavailable during this audit.
5. Resource scheduler tests disagree with the currently configured one-desktop capacity and assume additional sessions. Container tests need a configured isolated restricted broker; the local Docker Linux engine was unavailable.
6. Laya is intentionally optional and checkpoint integrity has been documented, but real model inference was not demonstrated in this runtime. Optional/offline fallback should remain clearly distinguished from a successful neural inference.

## Environment snapshot

| Check | Result | Limits |
|---|---|---|
| Python suite | 376 collected; 334 passed, 31 failed, 11 skipped; 5 warnings | Failures span Docker broker provisioning, phase 2/5/6/7 contracts, cognitive-fabric loops, and one-vs-four resource capacity assumptions. See `NEURO_TEST_EVIDENCE.md`. |
| Ruff | 7 findings | 6 import-order findings and 1 unused import; details recorded in evidence report. |
| Dashboard production build | See `NEURO_TEST_EVIDENCE.md` | Build runs Next.js compile, lint/type validation, and static generation. |
| Python mypy | See `NEURO_TEST_EVIDENCE.md` | A run was attempted; only claim a result if a completed process result is captured there. |
| Docker engine | Unavailable | Docker Desktop Linux engine named pipe was absent. No broker-backed execution can be verified locally. |
| Core API | Unavailable on ports 12000/12003 | No live employee/task workflow or API-backed browser acceptance in this environment. |
| Existing services | Ports 12001 and 12002 occupied | Their owning processes/workspaces are not ANCOS services; left untouched. |
| Performance | No valid baseline | No reproducible live API or worker load test was run; do not infer latency/capacity from unit tests. |

## Acceptance verdict

NEURO-0 records a local baseline and prioritizes the gaps. It does **not** accept the system as production-ready or autonomous. The capability labels and evidence hierarchy are in `NEURO_CAPABILITY_MATRIX.md`; the master design is in `NEURO_ARCHITECTURE.md`; remediation order and the complete user-provided NEURO-0–13 sequence are in `NEURO_PHASE_PLAN.md`.

## NEURO-1 follow-up (2026-11-02)

The control-plane slice now requires an owner identity on `POST /boss/objective`, invokes Boss staffing when verified skills or idle capacity are insufficient, records separate owner-submission and Boss-staffing audit events, and disables direct employee creation. The fixed-name bootstrap helper now generates and reuses dynamic profiles. The owner-only project report reads actual task, attempt, and verifier evidence. Five latest focused tests pass, including production-mode credential rejection, recruitment, and report accuracy. This does not satisfy the NEURO-1 end-to-end acceptance mission because a live task consumer and verifier are still unavailable; see current phase status and evidence report.

## Current repository delta (2026-11-03)

- **NEURO-1:** The dated follow-up above supersedes the initial audit-time recruitment finding; current objective-route staffing is owner-authenticated and focused-tested. No live objective-to-verified-report mission exists.
- **NEURO-2/3:** `455f752` adds an opt-in terminal-only worker, supported-mode claim filter, start/heartbeat/submit/fail protocol, lease-checked Core API command endpoint, and restricted-broker execution. `395f53d` fixes verifier rejection lifecycle: preserve the rejected receipt, consume bounded retry budget with persisted backoff, increment fencing, and fail terminally on exhaustion. The verifier replays only declared pytest/npm-test commands and now records a bounded path/size/SHA-256 workspace snapshot through an attempt-fenced endpoint. The snapshot is verification-time metadata only, not a task-start diff or correctness review. Latest focused regression passes (36 tests). Ruff, scoped mypy, Compose parsing, and Docker runtime validation are pending for this change; the Linux Docker engine was previously unavailable, so no live consumer receipt exists.
- **Roadmap fidelity:** The phase plan/status now preserve the user's full NEURO-0–NEURO-13 objectives. This corrects the prior shortened 0–9 plan, which did not represent the complete master mission.
- **Validation freshness:** the last full-suite result is still the older `c979cb2` run (339 passed, 32 failed, 11 skipped). Current `main` has focused checks only; do not treat historical full-suite results as current-source regression proof.
