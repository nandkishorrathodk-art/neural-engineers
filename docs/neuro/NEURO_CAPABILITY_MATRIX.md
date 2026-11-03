# ANCOS capability matrix

Audit baseline: 2026-11-02. Status describes evidence available in the repository and current local environment, not roadmap intent.

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
| Task worker process / durable consumer | PARTIALLY_IMPLEMENTED | Optional `workers` Compose profile runs a terminal-only LLM consumer with bounded argv, capability-filtered claims, Core API lease checks, broker-mediated execution, heartbeat, and submission for independent verification. It is not enabled by default or runtime-verified; GUI/hybrid workers, verifier consumer, and live receipts are missing. |
| Independent verifier process | MISSING | Verification receipt/gate and queue APIs exist; no separately running verifier consumer in Compose. |
| Restricted Docker broker boundary | PARTIALLY_IMPLEMENTED | Dedicated broker service, internal network, dropped caps, no-new-privileges, and scoped mounts are declared. Local engine absent; broker provisioning tests fail without configured broker. |
| Employee desktop provisioning and session leases | BLOCKED_BY_EXTERNAL_DEPENDENCY | Code and lease machinery exist; no local Docker engine; scheduler tests reveal 1-slot vs 4-slot configuration mismatch. |
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
| Operational observability and capacity | PARTIALLY_IMPLEMENTED | Observability and resource modules exist; worker heartbeat and performance SLO baseline are absent. |

## Verification boundary

Python component tests: 334 passed, 31 failed, 11 skipped. Passing cases support only their tested contracts. External Docker, core API, task worker, verifier, and model runtime were unavailable; therefore no “BUILT_AND_VERIFIED” label is assigned to the complete autonomous company workflow.
