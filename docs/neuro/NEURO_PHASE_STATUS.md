# ANCOS NEURO phase status

Updated 2026-11-02.

| Phase | Status | Evidence |
|---|---|---|
| NEURO-0 — Audit | BASELINE_RECORDED | This documentation set; full test suite, Ruff, and dashboard build evidence in `NEURO_TEST_EVIDENCE.md`. |
| NEURO-1 — Control-plane authority | IN_PROGRESS | Boss objective route requires owner identity, recruits for verified-skill/capacity gaps, and records owner and Boss actions separately; direct `POST /employees` is disabled. Owner-only project report includes task/attempt states and verifier evidence. Latest full suite: 339 passed, 32 failed, 11 skipped; changed-file Ruff passes. Real worker/verifier execution remains unavailable. |
| NEURO-2 — Durable execution | IN_PROGRESS | An opt-in terminal-only consumer now uses capability-filtered claims, start/heartbeat/submit/fail protocol, lease-checked Core API command execution through the restricted broker, and bounded retries/fencing/backoff. Unit tests and Compose rendering pass. Worker profile has not been run; in-flight broker cancellation, live service health/shutdown, GUI/hybrid modes, and crash-recovery receipts remain unverified; G-01 remains open. |
| NEURO-3 — Independent verification | BLOCKED | No deployed verifier consumer; G-01. |
| NEURO-4 — Restricted desktop workflow | BLOCKED | Local Docker engine unavailable; G-04. |
| NEURO-5 — First real task acceptance | NOT_ACCEPTED | No real employee task and independent receipt; G-02. |
| NEURO-6 — Intelligence and memory | NOT_STARTED | Inference and live provenance proof absent; G-08/G-11. |
| NEURO-7 — Reliability and recovery | NOT_STARTED | No performance baseline or clean-room restore receipt; G-09/G-12. |
| NEURO-8 — Product and operations | PARTIAL | Backend-driven code-rendered view exists; live API, persisted desk/speaker events absent. |
| NEURO-9 — Controlled evolution | NOT_STARTED | No production promotion receipt/rollback evidence. |

Statuses change only when supporting receipts are added. A mock, skipped test, or source module is not an acceptance receipt.
