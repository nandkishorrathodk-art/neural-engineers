# Task worker claim protocol

The API exposes a service-authenticated worker protocol under `/internal/worker`. Configure `WORKER_API_TOKEN` with at least 32 random characters; this credential is separate from the owner token. The credential currently identifies the worker service as a whole, not individual worker processes.

1. `POST /internal/worker/tasks/claim` with `{"worker_id":"worker-instance-id"}` leases one assigned, dependency-ready task. The API requires the assigned employee to be reserved and their persisted workstation to be running, then refreshes the workstation status through the restricted Docker broker.
2. The response includes a monotonically increasing `fencing_token`, attempt number, task acceptance criteria, employee ID, and workstation container ID.
3. `POST /internal/worker/tasks/{task_id}/start` must present the same worker ID and fencing token. It rechecks the live workstation and only then records `RUNNING`.
4. Heartbeats extend a live lease. A new claim can take over only after lease expiry or fencing-token revocation; stale workers cannot renew or submit.
5. `POST /submit` stores a nonempty execution receipt on the attempt and returns `submitted_for_verification`. It deliberately leaves the task `RUNNING`; only an independent verifier can complete it. `/fail` records failure and releases the employee.

The separately credentialed verifier protocol uses `VERIFIER_API_TOKEN` (also at least 32 random characters); owner, worker, and verifier tokens must all be different. `GET /internal/verifier/queue` returns current submitted attempts and their acceptance criteria. `POST /internal/verifier/tasks/{task_id}/attempts/{attempt_id}/review` stores a verdict, notes, evidence references, and checks. A passing decision atomically records the receipt, `TaskResult`, completion state, and fencing-token change. A rejected attempt remains available for a later worker retry. Worker and verifier credentials are separate service-wide credentials; individual process identity and verifier execution/attestation are not yet bound to these bearer tokens.

The lease and attempt state share a database transaction with task fencing updates. Task assignment, external GUI actions, and outbox insertion are still not one atomic transaction. Redis message delivery does not execute tasks.

There is not yet a task-worker or verifier process in Compose, nor a production executor connected to the employee desktop. Development mode can use a synthetic desktop for simulations, but production GUI capture rejects synthetic/mock frames and returns unavailable because no live screenshot-based workstation adapter is configured. Until a real desktop adapter and independently operated verifier are deployed, these endpoints are only protocol surfaces and the system must not claim autonomous work has run or completed. A stale/unknown workstation is not leased.
