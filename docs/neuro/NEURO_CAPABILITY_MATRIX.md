# ANCOS capability matrix

Audit baseline: 2026-11-02; implementation status updated through 2026-11-03. Status describes evidence available in the repository and current local environment, not roadmap intent.

## Status vocabulary

- **BUILT_AND_VERIFIED** — implemented and supported by a passing focused test or observed runtime receipt for the stated scope.
- **BUILT_BUT_NOT_VERIFIED** — implementation exists, but no adequate passing test/runtime evidence was available.
- **PARTIALLY_IMPLEMENTED** — meaningful code exists, with a known missing part in the capability chain.
- **MOCK_OR_SIMULATED** — evidence comes from mocks, synthetic data, fallback behavior, or a protocol stub.
- **MISSING** — no implementation/deployed component found for the capability.
- **BLOCKED_BY_EXTERNAL_DEPENDENCY** — verification/execution requires a service, model, broker, or hardware not available in this audit.

## Matrix

| Capability | Status | Evidence / boundary |
|---|---|---|
| Boss objective planning and structured plan validation | PARTIALLY_IMPLEMENTED | Planner/DAG persistence exists. The owner-authenticated objective route now enables staffing decisions; lower-level calls default recruitment off. No live deployment plan was run. |
| Owner-facing project execution report | PARTIALLY_IMPLEMENTED | Read-only report uses persisted task/attempt states and distinguishes passing verifier receipts from legacy unverified completion; API requires owner identity. It has in-process DB tests, not a deployed workflow receipt. |
| Dynamic employee records and factory | PARTIALLY_IMPLEMENTED | Factory generates dynamic identities; Boss recruits for missing verified skills or idle capacity. Direct `POST /employees` is disabled; explicit local bootstrap is dynamic/idempotent. No live deployed recruitment was run. |
| Project/task DAG and API state | PARTIALLY_IMPLEMENTED | DAG/scheduler/task APIs exist; phase 2 and API contract failures remain. A running consumer is not established. |
| Task worker process / durable consumer | PARTIALLY_IMPLEMENTED | Optional `workers` Compose profile runs a terminal-only LLM consumer with bounded argv, capability-filtered claims, Core API lease checks, broker-mediated execution, heartbeat, and submission for independent verification. One controlled live protocol cycle used `TerminalWorker.execute_claim` via a local harness and fixed planner stub against a live desktop; task was manually seeded, so Boss/provider planning and long-running Compose consumer behavior remain unverified. GUI/hybrid workers and lifecycle failure-path receipts are missing. |
| Independent verifier process | PARTIALLY_IMPLEMENTED | One controlled live protocol cycle passed independent verification using a separate verifier credential and a disposable network-disabled container with read-only employee workspace, replaying `python3 -m unittest -v` and recording a nonempty bounded path/size/SHA-256 snapshot. The employee image lacked pytest. Long-running profile operation, retry/loss/cancellation/shutdown behavior are unverified. Snapshot is captured at verification time and establishes neither a task-start diff nor artifact correctness; criteria remain exact test-only contracts. |
| Restricted Docker broker boundary | PARTIALLY_IMPLEMENTED | Dedicated broker service, internal network, dropped caps, no-new-privileges, and scoped mounts are declared. Local engine absent; broker provisioning tests fail without configured broker. |
| Employee desktop provisioning and session leases | PARTIALLY_IMPLEMENTED | Three sequential current-source production-mode cycles prove distinct owner/approver approvals, real broker provisioning, non-root/capability/socket/workspace isolation, lease renewal, 1280×800 capture, approved termination, lease release, and audit-chain integrity. One cycle restarted the isolated API while the desktop stayed alive, then recovered it from DB/broker and captured the screen. A separate planned broker stop returned `runtime_unavailable` while retaining the active lease; broker restart restored the running desktop and capture, followed by approved termination and valid audit verification. A 1 CPU / 1 GiB idle run sampled 20 points (peak 147.4 MiB RAM / 0.58% CPU), not task load. Verified checkpoint payload handoff works across a fresh scheduler instance in tests; no live task used one. SQLite tests prove two-slot admission/exhaustion. Concurrent task load, multi-host, abrupt-crash, and live checkpoint-resumption proof remain open. |
| GUI perception, action, recovery, verification modules | PARTIALLY_IMPLEMENTED | Modules and component tests exist; the previous audit reports no deployed live screenshot/action adapter for task execution. |
| Live GUI task completed by an employee | MISSING | No end-to-end execution receipt exists. |
| LLM gateway / provider routing | PARTIALLY_IMPLEMENTED | Provider tiers, retry/circuit-breaker logic exist; normal tests use injected providers; live inference is configuration-dependent. |
| Laya / neural fast decision inference | BLOCKED_BY_EXTERNAL_DEPENDENCY | Optional by design; pinned checkpoint hash was documented, but weights/runtime inference were not exercised here. Heuristic fallback is not neural inference. |
| Retrieval, memory, GraphRAG | PARTIALLY_IMPLEMENTED | Modules and tests exist; no verified live cross-employee provenance/learning loop was demonstrated. |
| Meeting lifecycle and communication records | PARTIALLY_IMPLEMENTED | API/data paths and visualization exist; polling is used, and persisted meeting speaker/presenter events are not wired. |
| Dashboard live company floor | PARTIALLY_IMPLEMENTED | Code-rendered scene maps backend employee/task/comms/meeting records; live backend unavailable. Role-to-desk mapping is local, not persisted assignment. |
| Autonomous software engineering loop | MISSING | No verified worker-to-artifact-to-independent-review delivery loop demonstrated. |
| Skill verification and learning loop | PARTIALLY_IMPLEMENTED | Skill/evolution services exist; cognitive-fabric loop tests fail and promotion evidence is absent. |
| Governance, approvals, audit trail | PARTIALLY_IMPLEMENTED | Approval/security/audit code and migrations exist; full privilege, failure, and recovery lifecycle was not runtime-tested here. |
| Backup and disaster recovery | PARTIALLY_IMPLEMENTED | Backup/restore modules and tests exist; no clean-room restore receipt was generated in this run. |
| Operational observability and capacity | PARTIALLY_IMPLEMENTED | Resource profiles expose 4/8/16 desktop ceilings with a deployment cap (`ANCOS_MAX_ACTIVE_DESKTOPS`, Compose default 1); approved API provisioning uses durable slot leases and broker/manager limits. No live multi-session load measurement or worker heartbeat/SLO baseline exists. |

## Verification boundary

NEURO-0 full-suite baseline on 2026-11-02: 334 passed, 31 failed, 11 skipped. A later full regression on source commit `c979cb2` reported 339 passed, 32 failed, 11 skipped; latest commits have focused lifecycle and verifier-contract coverage, not a new full-suite run. Passing cases support only their tested contracts. External Docker, core API, task worker, verifier, and model runtime were not demonstrated live; therefore no `BUILT_AND_VERIFIED` label is assigned to the complete autonomous company workflow. See `NEURO_TEST_EVIDENCE.md` for commit and scope boundaries.
