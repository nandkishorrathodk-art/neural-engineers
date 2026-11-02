# ANCOS NEURO-0 system audit

**Audit date:** 2026-11-02

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

NEURO-0 records the current baseline and prioritizes the gaps. It does **not** accept the system as production-ready or autonomous. The capability labels and evidence hierarchy are in `NEURO_CAPABILITY_MATRIX.md`; the master design is in `NEURO_ARCHITECTURE.md`; remediation order is in `NEURO_PHASE_PLAN.md`.

## NEURO-1 follow-up (2026-11-02)

The first control-plane slice now requires an owner identity on `POST /boss/objective`, invokes Boss staffing when verified skills or idle capacity are insufficient, records separate owner-submission and Boss-staffing audit events, and disables direct employee creation. The fixed-name bootstrap helper now generates and reuses dynamic profiles. Seven focused tests pass, including production-mode credential rejection and isolated API recruitment. This does not satisfy the NEURO-1 end-to-end acceptance mission because a live task consumer and verifier are still unavailable; see current phase status and evidence report.
