# ANCOS NEURO architecture baseline

This document describes the intended system shape as constrained by current code and repository operational rules. It marks the difference between an existing module and a proven running service.

## Runtime boundaries

```text
Owner / Dashboard
       │ authenticated objective, approvals, status
       ▼
Core API ── Boss / planner ── project DAG + task records
   │                              │
   │                              ▼
   │                      durable task claim/lease
   │                              │
   ├── policy + audit             ▼
   ├── LLM / optional Laya   Task worker (not deployed)
   ├── memory/retrieval           │ GUI-only actions
   ├── comms/meetings              ▼
   └── verifier queue       Restricted Docker broker
              │                    │
              └── independent verifier (not deployed)
                                   ▼
                         isolated employee desktop
```

The diagram is a target/known-module view. The task worker and verifier are explicitly not demonstrated as running consumers. The separate restricted Docker broker is declared in Compose and is the only component intended to control Docker. Core API and dashboard must not gain direct unrestricted Docker access.

## Architectural pillars and current state

1. **Sovereign control plane:** owner requests enter authenticated API routes; Boss planning proposes work; policy and approval gates authorize privileged steps; hash-chained audit records preserve actions. Prove actual route-to-policy enforcement before enabling automation.
2. **Persistent organization state:** relational records hold employees, projects, tasks, messages, approvals, verification receipts, and resource leases. Migration history is maintained via Alembic. Confirm migrations on a clean disposable database before each release.
3. **Execution plane:** workers claim bounded tasks, use employee-scoped workspaces and GUI-only desktop actions where assigned, and publish artifacts/receipts. Consumer, heartbeat, timeout, retry, fencing, and shutdown semantics remain an acceptance gap.
4. **Independent verification plane:** a separate identity/process obtains task evidence, validates artifacts and acceptance criteria, and emits a receipt that gates completion. Worker self-report alone must never mark work verified.
5. **Cognition:** LLM gateway supports reasoning/coding/fast tiers with budgets and fallback; optional Laya inference is advisory. Neither model output can override policy or approval gates.
6. **Memory and evolution:** retrieval, GraphRAG, skill/method evaluation, and evolution modules provide candidate improvements. Promotion requires reproducible evidence, isolated evaluation, approval where required, and rollback.
7. **Human operations:** dashboard reads persisted company state; status must distinguish unavailable from empty. GUI scene, characters, and activity must never invent employee records, messages, meeting events, or successful work.

## Trust and failure boundaries

- Treat model output, worker claims, retrieved content, and desktop-visible content as untrusted input.
- Use separate owner, worker, verifier, and broker credentials with task-scoped authorization; do not copy owner secrets into employee containers.
- Enforce broker allowlists, confined workspace mounts, `cap_drop: ALL`, `no-new-privileges`, and network boundaries. Do not expose `/var/run/docker.sock` outside the broker.
- Make retries idempotent and fence stale attempts so a timed-out worker cannot overwrite a newer result.
- Fail closed for missing approval, unavailable broker, missing verifier receipt, invalid artifacts, or uncertain execution state.
- Keep audit logs useful without recording secrets, raw tokens, or sensitive desktop content beyond approved retention.

## Evidence-linked implementation map

See the [capability matrix](NEURO_CAPABILITY_MATRIX.md) and [gap register](NEURO_GAP_REGISTER.md). Runtime service inventory is in root `docker-compose.yml`; the operational evidence and non-acceptance result are summarized in `NEURO_SYSTEM_AUDIT.md` and `NEURO_TEST_EVIDENCE.md`.
