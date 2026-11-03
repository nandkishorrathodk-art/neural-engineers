# ANCOS NEURO operations baseline

## Deployment inventory

The checked-in `docker-compose.yml` declares Redis, a dedicated restricted Docker broker, core API, dashboard, an opt-in terminal-only worker under `workers`, and a separate terminal test verifier under `verifiers`. Both consumers are opt-in and neither has a live runtime receipt. The local audit could not inspect a live ANCOS API or Docker engine; unrelated listeners on ports 12001 and 12002 were left untouched.

## Operator requirements before a pilot

- Confirm owner, approver, worker, verifier, and broker secrets are independently provisioned and never log/print them.
- Apply migrations against a disposable database first; verify backup and rollback path.
- Ensure broker health and policy allowlists before creating employee desktops.
- Desktop profiles advertise up to 4/8/16 concurrent sessions for 16/32/64 GB profiles. `ANCOS_MAX_ACTIVE_DESKTOPS` sets a lower runtime ceiling; Compose defaults it to `1`. Raise it only after measuring available host headroom and confirming broker capacity. This scheduler ceiling does not itself provision more Docker capacity or establish a safe workload benchmark.
- When multiple scheduler processes share one desktop host, configure the same stable `ANCOS_RESOURCE_POOL_ID` for all of them. Give each independent host a different pool ID when they share a database. The scheduler only gets durable fencing when constructed with a `ResourceLeaseRepository`; the current Compose service does not yet instantiate that scheduler path. A released lease does not by itself clean up a container after process loss.
- To opt into terminal execution and independent test replay, configure distinct owner, worker, verifier, and broker credentials plus an LLM provider, then start the `workers` and `verifiers` Compose profiles together. For example: `docker compose --profile workers --profile verifiers up --build`. Each consumer has CPU/memory/PID bounds and no Docker broker token. Verifier tests run in a temporary network-disabled container with the employee workspace mounted read-only and a 300-second cap; only declared `pytest`/`npm test` commands are replayed. This is not a general semantic/artifact verifier. Do not treat either profile as production-accepted until live broker, isolation, cancellation, shutdown, criteria, artifact, and receipt tests pass.
- Do not consume stale or underspecified tasks automatically. Require explicit objective and acceptance criteria.
- Monitor queue age, lease expiry, worker/verifier heartbeats, failed attempts, broker denials, cost, resource saturation, audit-chain integrity, backup age, and API error rates.
- On uncertain task state, stop retries, preserve workspace/artifacts, and require review; never mark task complete without a verifier receipt.

Performance, restoration objectives, alert thresholds, and support ownership remain undefined until NEURO-12 produces measured evidence. See `NEURO_GAP_REGISTER.md`.
