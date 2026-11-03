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
   ├── LLM / optional Laya   Terminal worker (opt-in Compose profile)
   ├── memory/retrieval           │ terminal argv via lease-checked API/broker
   ├── comms/meetings              ▼
   └── verifier queue       Restricted Docker broker
              │                    │
              └── terminal test verifier (opt-in Compose profile)
                                   ▼
                         isolated employee desktop
```

The diagram is a target/known-module view. A terminal-only task worker and terminal test verifier are available behind separate opt-in `workers` and `verifiers` Compose profiles. One controlled current-source worker-to-verifier protocol cycle has been demonstrated with a manually seeded task and local planner stub; the long-running Compose profiles and a Boss objective-to-report mission have not. The verifier replays declared pytest, Python unittest, or npm-test commands under a separate credential in a disposable, network-disabled container with a read-only employee-workspace mount; it fails closed unless acceptance criteria exactly match a supported test-only contract. It does not verify artifact content. The separate restricted Docker broker is declared in Compose and is the only component intended to control Docker. Core API and dashboard must not gain direct unrestricted Docker access.

## Architectural pillars and current state

1. **Sovereign control plane:** owner requests enter authenticated API routes; Boss planning proposes work; policy and approval gates authorize privileged steps; hash-chained audit records preserve actions. Prove actual route-to-policy enforcement before enabling automation.
2. **Persistent organization state:** relational records hold employees, projects, tasks, messages, approvals, verification receipts, and resource leases. Migration history is maintained via Alembic. Confirm migrations on a clean disposable database before each release.
3. **Execution plane:** the optional terminal consumer claims only terminal-mode tasks, heartbeats its lease, sends bounded argv through a lease-checked Core API endpoint, and submits command receipts for independent review. The desktop scheduler writes checksummed task checkpoints and returns the complete verified payload to a fresh scheduler instance; legacy checkpoints remain readable but are not auto-resumed. GUI/hybrid execution, process health, interruption of in-flight broker commands, shutdown receipt, live checkpoint resumption, abrupt API/broker crash recovery remain acceptance gaps; one controlled API restart and one planned broker stop/restart each recovered a live desktop in isolation.
4. **Independent verification plane:** a separate identity/process can replay declared test commands and collect a bounded SHA-256 path/size snapshot through the broker in a disposable offline container with a read-only workspace mount. The snapshot is taken at verification time; it does not prove which files changed during the task or whether file contents satisfy requirements. Verification still accepts only a small exact allowlist of test-only criteria and does not verify arbitrary semantics. Worker self-report alone must never mark work verified.
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
