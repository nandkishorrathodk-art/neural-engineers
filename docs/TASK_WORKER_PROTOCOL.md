# Task worker claim protocol

The API exposes a service-authenticated worker protocol under `/internal/worker`. Configure `WORKER_API_TOKEN` with at least 32 random characters; this credential is separate from the owner token. The credential currently identifies the worker service as a whole, not individual worker processes.

1. `POST /internal/worker/tasks/claim` with `{"worker_id":"worker-instance-id"}` leases one assigned, dependency-ready task. The API requires the assigned employee to be reserved and their persisted workstation to be running, then refreshes the workstation status through the restricted Docker broker.
2. The response includes a monotonically increasing `fencing_token`, attempt number, task acceptance criteria, employee ID, and workstation container ID.
3. `POST /internal/worker/tasks/{task_id}/start` must present the same worker ID and fencing token. It rechecks the live workstation and only then records `RUNNING`.
4. Heartbeats extend a live lease. A new claim can take over only after lease expiry or fencing-token revocation; stale workers cannot renew or submit.
5. `POST /submit` stores a nonempty execution receipt on the attempt and returns `submitted_for_verification`. It deliberately leaves the task `RUNNING`; only an independent verifier can complete it. `/fail` records failure and releases the employee.

The lease and attempt state share a database transaction with task fencing updates. Task assignment, external GUI actions, and outbox insertion are still not one atomic transaction. Redis message delivery does not execute tasks.

There is not yet a task-worker process in Compose, nor a production executor connected to the employee desktop. The controller currently generates a synthetic desktop when no mock frame is supplied, so it is not suitable as a production executor. Until a real screenshot-based workstation adapter and independent verifier are deployed, claims are only a protocol surface and the system must not claim work has run or completed. A stale/unknown workstation is not leased.
