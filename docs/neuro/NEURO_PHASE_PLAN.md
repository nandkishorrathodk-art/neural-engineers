# ANCOS NEURO phase plan

This is a sequential delivery plan, not a claim that future work has been done. Do not combine all phases into one rewrite. Each phase needs a reviewed diff, focused evidence, and a recorded status.

| Phase | Objective | Main deliverables | Exit gate |
|---|---|---|---|
| NEURO-0 — Audit (baseline recorded) | Establish truthful baseline | System audit, capability matrix, architecture baseline, gap register, evidence report, phase plan | Baseline commands/results and blockers are recorded; no capability overstated. |
| NEURO-1 — Control-plane authority (in progress) | Prove owner/Boss/policy/approval boundaries | Owner-authenticated objectives, verified-skill/capacity recruitment decisions, direct-hire closure, attributable audit events, owner-only persisted project report | Negative authorization tests pass; only owner-submitted objectives can trigger Boss recruitment; real multi-step execution/recovery/report still requires worker/verifier phases. |
| NEURO-2 — Durable execution | Build bounded worker lifecycle | Task consumer, lease/fencing, heartbeat, retry, cancellation, idempotency, outbox integration | Disposable integration test recovers worker loss without duplicate/unverified completion. |
| NEURO-3 — Independent verification | Run separate verifier identity and consumer | Artifact collection, criteria evaluation, immutable receipt, completion gate | Worker cannot self-verify; invalid/missing receipt blocks completion. |
| NEURO-4 — Restricted desktop workflow | Exercise broker and GUI lifecycle | Broker CI environment, fresh employee desktop, visual interaction adapter, evidence capture | Security assertions pass and one task produces reviewable screen/artifact evidence. |
| NEURO-5 — First real task acceptance | Prove complete vertical slice | Isolated DB, objective, recruitment, task, desktop work, verifier, dashboard result, cleanup | Owner-approved scoped task is independently verified with receipts; no synthetic progress. |
| NEURO-6 — Intelligence and memory | Connect model, retrieval, and learning with provenance | LLM/Laya receipts, memory source lineage, privacy/retention, controlled skill/method evaluation | Model/fallback clearly identified; no unverified memory or skill is promoted. |
| NEURO-7 — Reliability and recovery | Establish capacity, SLOs, fault recovery | Resource capacity model, load tests, backup/restore drill, observability/runbooks | P95/P99, throughput, cost, RTO/RPO measured on declared environment; recovery drill passes. |
| NEURO-8 — Product and company operations | Make dashboard reflect persistent reality and support humans | Persisted workstation/meeting events, event freshness, approval UX, accessibility | Browser evidence matches API records and reports unavailable/stale states truthfully. |
| NEURO-9 — Controlled evolution | Promote only verified improvements | Evaluation harness, isolated candidate staging, approval, rollback, audit | Reproducible improvement receipts and rollback test; no self-modifying production path without gate. |

## Working rules

- Do not advance a phase based on source presence alone. Require the phase exit evidence.
- Keep existing dashboard edits and user data intact. Use disposable databases and explicitly isolated test resources.
- Do not start autonomous execution on underspecified/stale tasks. Require approved scope and acceptance criteria.
- Mark unavailable dependencies as blocked with exact evidence; never substitute mocks and label them live.
- Update the phase status and evidence files whenever an exit gate changes.
