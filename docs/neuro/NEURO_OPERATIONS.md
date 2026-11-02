# ANCOS NEURO operations baseline

## Deployment inventory

The checked-in `docker-compose.yml` declares Redis, a dedicated restricted Docker broker, core API, and dashboard. It does not declare task-worker or verifier consumers. The local audit could not inspect a live ANCOS API or Docker engine; unrelated listeners on ports 12001 and 12002 were left untouched.

## Operator requirements before a pilot

- Confirm owner, approver, worker, verifier, and broker secrets are independently provisioned and never log/print them.
- Apply migrations against a disposable database first; verify backup and rollback path.
- Ensure broker health and policy allowlists before creating employee desktops.
- Start worker and verifier only with scoped tokens, resource budgets, heartbeat, kill/cancel path, and independently tested receipts.
- Do not consume stale or underspecified tasks automatically. Require explicit objective and acceptance criteria.
- Monitor queue age, lease expiry, worker/verifier heartbeats, failed attempts, broker denials, cost, resource saturation, audit-chain integrity, backup age, and API error rates.
- On uncertain task state, stop retries, preserve workspace/artifacts, and require review; never mark task complete without a verifier receipt.

Performance, restoration objectives, alert thresholds, and support ownership remain undefined until NEURO-7 produces measured evidence. See `NEURO_GAP_REGISTER.md`.
