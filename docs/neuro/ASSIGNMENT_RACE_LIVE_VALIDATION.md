# NEURO-12A Post-Fix Reliability Report
> **Scope of this file.** This is a raw NEURO-12A batch-runner report, not a new
> milestone. It was produced to live-validate the assignment/provisioning-race fix
> (stale-assignment recovery + PENDING re-dispatch + provisioning guard) against the
> running stack. Read the numbers honestly:
>
> - **GUI: 3/3 completed and independently verified.** The provisioning-race guard
>   worked live: no task was requeued mid-provisioning, and the worker's first
>   successful attempt was claimed and verified (retry frequency 0.0).
> - **Hybrid: 0/3.** All three failed the verifier's hybrid-terminal-replay check.
>   This is **not** caused by the race fix. Inspecting the receipts and the employee
>   workspace volume shows the GUI edit phase typed its line but the document was
>   never saved, so `handoff.txt` is missing the `GUI_EDIT=HYBRID_GUI_OK` line the
>   replay test asserts. This is the same worker-layer GUI-save flake already
>   documented in `NEURO12A_SUSTAINED_RELIABILITY_REPORT.md` ("What the remaining
>   failures actually are") and is left unfixed here.
> - The run used the `workers` **and** `verifiers` compose profiles. A first attempt
>   without the verifier profile left attempts stuck in `submitted`; that attempt was
>   discarded, not reported.
>
> So the fix is validated for the GUI path and is not the cause of the hybrid misses;
> the hybrid flake remains an open, separately-tracked reliability gap.



Generated (UTC): 2026-11-14T19:33:50.753305+00:00

## Environment fingerprint

- Branch: `main`
- HEAD commit: `58a4cf8651e22ec8f1f51e789b7cbb64101b9e02` — Record final NEURO-12A scoped post-fix cohort evidence
- Required fix commit `54a256c` present in tested code: **True**
- Desktop image: `ancos/employee-base:latest`
- Worker config: `{"service_runtimes": [{"age_seconds": 3.23, "instance_id": "ba7879bca4ff4deba991b98803a513a0", "last_seen_at": "2026-11-14T19:19:19.532345", "service_id": "verification-service", "service_type": "verifier", "status": "healthy"}, {"age_seconds": 6.28, "instance_id": "dbb58cc89bfa47a388c46a3baf7dff15", "last_seen_at": "2026-11-14T19:19:16.484502", "service_id": "worker-service", "service_type": "worker", "status": "healthy"}], "verifier_auth_configured": true, "verifier_runtime": "healthy", "worker_auth_configured": true, "worker_runtime": "healthy"}`
- Model config: `{"routes": {"boss_brain": "reasoning", "code_debugging": "coding", "code_generation": "coding", "code_review": "reasoning", "independent_verifier": "reasoning", "log_summarization": "fast", "meeting_moderator": "reasoning", "status_reporting": "fast", "task_decomposer": "reasoning"}, "tiers": {"coding": {"fallbacks": ["nim/nvidia/nemotron-3.5-bolt-e4b", "openai/gpt-6.1-sol", "anthropic/claude-sonnet-5-5", "gemini/gemini-3.5-flash"], "primary": "nim/nvidia/nemotron-3.5-lightning-30b-a3b"}, "fast": {"fallbacks": ["nim/nvidia/nemotron-3.5-lightning-30b-a3b", "openai/gpt-6-luna", "anthropic/claude-haiku-5-5", "gemini/gemini-3.5-flash-lite"], "primary": "nim/openai/gpt-oss-20b"}, "reasoning": {"fallbacks": ["nim/nvidia/nemotron-3.5-bolt-e4b", "openai/gpt-6.1-sol", "anthropic/claude-sonnet-5-5", "gemini/gemini-3.5-flash"], "primary": "nim/nvidia/nemotron-3.5-lightning-30b-a3b"}}}`

## Results by mode and cohort

| Cohort | Mode | Samples | Counted | Completed | Failed | Cancelled | Timed out | In flight | Contract violations | Success rate | Timeout rate | Retry freq | Mean latency (s) | p95 latency (s) | Cost (USD) | Cost/sample | Peak CPU % | Peak mem MiB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| smoke | gui | 3 | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 100% | 0% | 0.0 | 46.8 | 71.35 | 0.0000 | 0.000000 | n/a | n/a |
| smoke | hybrid | 3 | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0% | 0% | 3.0 | 242.5 | 246.33 | 0.0088 | 0.002932 | n/a | n/a |

Statistical samples are unique objectives. Retries are reported separately as retry frequency and never inflate the sample count. In-flight, pending and timed-out objectives are kept in their own columns and never counted as successes.

## Failure reasons (verbatim, grouped)

### smoke / gui

- (1x) Independent visual verification passed: the verifier observed the live employee desktop itself and its frame matched the worker's post-action frame (exact match), the worker's GUI loop reported success, and the verifier read the required on-screen deliverable terms ['NEURO12A-GUI-001'].
- (1x) Independent visual verification passed: the verifier observed the live employee desktop itself and its frame matched the worker's post-action frame (exact match), the worker's GUI loop reported success, and the verifier read the required on-screen deliverable terms ['NEURO12A-GUI-002'].
- (1x) Independent visual verification passed: the verifier observed the live employee desktop itself and its frame matched the worker's post-action frame (exact match), the worker's GUI loop reported success, and the verifier read the required on-screen deliverable terms ['NEURO12A-GUI-003'].

### smoke / hybrid

- (12x) Independent visual verification failed closed: the live desktop could not be observed, the independently captured frame did not match the worker's post-action frame, the worker's GUI loop did not report success, the required on-screen deliverable terms were not read by the verifier, or a hybrid terminal phase was not independently replayable.

## Pre-fix vs post-fix comparison

No comparison requested.

## Preserved evidence

Every objective's execution receipts, verification failure reasons and resource samples are preserved in `docs/neuro/evidence/neuro12c_validation.json`.

Objectives recorded: 6
