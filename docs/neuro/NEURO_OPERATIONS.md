# ANCOS NEURO operations baseline

## Deployment inventory

The checked-in `docker-compose.yml` declares Redis, a dedicated restricted Docker broker, core API, dashboard, and an opt-in terminal-only worker under the `workers` profile. It does not declare a verifier consumer. The local audit could not inspect a live ANCOS API or Docker engine; the worker profile has not been run, and unrelated listeners on ports 12001 and 12002 were left untouched.

## Operator requirements before a pilot

- Confirm owner, approver, worker, verifier, and broker secrets are independently provisioned and never log/print them.
- Apply migrations against a disposable database first; verify backup and rollback path.
- Ensure broker health and policy allowlists before creating employee desktops.
- To opt into terminal execution, configure distinct owner, worker, verifier, and broker credentials plus an LLM provider, then start the `workers` Compose profile. The worker has CPU/memory/PID bounds, no Docker broker token, and submits work for independent verification. Do not treat it as production-accepted until live broker, cancellation, verifier, and receipt tests pass.
- Do not consume stale or underspecified tasks automatically. Require explicit objective and acceptance criteria.
- Monitor queue age, lease expiry, worker/verifier heartbeats, failed attempts, broker denials, cost, resource saturation, audit-chain integrity, backup age, and API error rates.
- On uncertain task state, stop retries, preserve workspace/artifacts, and require review; never mark task complete without a verifier receipt.

Performance, restoration objectives, alert thresholds, and support ownership remain undefined until NEURO-7 produces measured evidence. See `NEURO_GAP_REGISTER.md`.
