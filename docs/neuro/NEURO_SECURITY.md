# ANCOS NEURO security baseline

## Current controls present in repository

- `docker-compose.yml` isolates a dedicated Docker broker on an internal control network, mounts the Docker socket only into that broker, drops capabilities, applies `no-new-privileges`, and requires a broker token.
- Core API and dashboard are configured with owner-token integration; worker, verifier, and approver credentials are represented as separate settings.
- The Boss objective route has an explicit owner-identity dependency. Staffing audit records preserve the authenticated owner as objective submitter and use `BossAI` only for the separate staffing decision.
- Direct `POST /employees` creation is disabled; recruiting occurs in Boss planning for an owner-submitted objective. Legacy local bootstrap profiles now have dynamic names/IDs and remain explicit helper calls.
- Repository guidance requires restricted broker use, scoped employee workspace mounts, no host-root mounts, and approval gates for destructive operations.
- Governance, approval consumption, audit-chain, and task fencing modules/migrations exist.

## Verification limits and required controls

Local Docker was unavailable, so zero-trust provisioning was not verified in a live broker. The broker integration tests failed when no broker was configured. Passing source inspection is not proof of runtime isolation.

Focused production-mode API tests verify that missing credentials and worker credentials cannot submit objectives, the owner credential can, and the resulting audit actors are distinct. These tests exercise the API in-process against an isolated test database; they do not prove a deployed runtime configuration.

Before enabling autonomous execution, require negative tests for missing/invalid/replayed approvals, credential separation, employee cross-workspace access, broker command/image/mount allowlists, stale lease fencing, and verifier independence. Never grant employee containers Docker socket access or owner credentials. Treat LLM, retrieval, GUI-visible text, and artifacts as untrusted. Keep policy and human approval authoritative over model decisions.
