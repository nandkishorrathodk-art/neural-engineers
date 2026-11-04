# NEURO-13 live acceptance runner

`uv run python -m neural.acceptance.neuro13` is an opt-in, real-service acceptance entrypoint for the owner objective → dynamic employee → approved workstation → terminal worker → independent verifier lifecycle. It does not start fake workers or synthesize receipts.

## Required disposable setup

Run only against a loopback ANCOS stack with a **disposable database and workspace volume**. The runner refuses non-loopback URLs, non-production mode, a nonempty project/employee/task database, unavailable provider-backed planning, absent/stale worker or verifier heartbeats, or missing credentials. It never cleans database rows or workspace volumes. After work completes, it terminates the workstation through a separately approved lifecycle action and leaves the named employee workspace volume under normal platform retention policy.

The run submits a real provider-backed objective and worker task, so model-provider usage may incur cost. `budget_usd` is not currently a hard-enforced objective-route limit. Review provider/account limits before opting in.

Set these variables in a dedicated disposable shell:

```powershell
$env:ANCOS_NEURO13_API_URL = "http://127.0.0.1:12000"
$env:ANCOS_NEURO13_DISPOSABLE_CONFIRM = "YES_I_WILL_DISCARD_THIS_INSTANCE"
$env:ANCOS_NEURO13_OWNER_TOKEN = "<disposable owner token>"
$env:ANCOS_NEURO13_APPROVER_TOKEN = "<different approver token>"
$env:ANCOS_NEURO13_TIMEOUT_SECONDS = "1800" # optional; 60–7200
$env:ANCOS_NEURO13_POLL_SECONDS = "5"        # optional; 1–30
uv run python -m neural.acceptance.neuro13
```

The API's disposable environment must already have a configured planning/worker LLM provider, one-or-more long-running terminal workers, a long-running independent verifier, and a healthy restricted Docker broker. Keep the normal service credentials scoped to that environment; the runner only needs owner and approver tokens. Do not point it at the everyday local or production database.

## Verdict semantics

- `PASS` requires a provider-backed Boss plan, a persisted dynamic employee/task, distinct approval for workstation provision and termination, all planned terminal tasks completed with independent verifier receipts, successful worker and verifier test exits, a nonempty complete workspace manifest and changed-file delta with SHA-256 references, and a valid audit chain.
- `BLOCKED` means a required live dependency, credential, disposable precondition, or owner report endpoint is missing/unavailable. The exit code is 2.
- `FAIL` means the real flow ran but violated its receipt/evidence contract or cleanup failed. The exit code is 1.

The report exposes only allowlisted model IDs/cost metadata, test command exit codes, verifier checks, artifact paths/hashes, and IDs needed for audit. It excludes raw prompts, model responses, credentials, and raw test output. A passing run exercises the available terminal worker/verifier lifecycle; it does **not** by itself prove the broader 21-step NEURO-13 mission features (GUI, hybrid/tool execution, meetings, cross-employee memory reuse, safe code promotion/rollback, or their integrated acceptance).
