# AGENTS.md — ANCOS Repository Memory & Operational Guidelines

## Project Overview & Mission
**ANCOS** (Autonomous Neural Company Operating System) is an autonomous, persistent, multi-agent AI software engineering enterprise. It takes high-level human objectives, decomposes them into structured project DAGs, dynamically recruits and assigns specialized AI employees, equips them with sandboxed desktop workstations, coordinates multi-agent collaboration, independently verifies deliverables, and continuously evolves its capabilities under strict human sovereignty.

ANCOS is a completely independent, production-grade autonomous organization.

---

## Architecture & Foundational Pillars

### 1. Boss AI (CEO / Executive Orchestration)
- Directs technical company strategy and mission decomposition (`BossBrain`, `TaskPlanner`).
- Decomposes natural language objectives into hierarchical execution DAGs (Projects -> Phases -> Tasks -> Subtasks).
- Assigns tasks to employees matching required verified skills and balances workload (`TaskDelegator`).
- Coordinates topological DAG execution with concurrency control and parallel dispatch limits (`TaskScheduler`).
- Interacts with human leadership through the Digital Boardroom and approval gates (`HumanApprovalGate`).

### 2. Dynamic AI Workforce & Desktops
- **Zero Predefined Employee Hardcoding**: Employees are dynamically created with unique IDs (`ANC-EMP-XXXX`), tailored roles, departments, system prompts, approved tools, and personal workspaces (`EmployeeFactory`).
- **Dedicated Container Workstations**: Provisioned via `RestrictedDockerBroker` with least privilege:
  - Container naming restricted to `ancos-desktop-anc-emp-[0-9a-z_-]+`.
  - Dropped Linux capabilities (`cap_drop=['ALL']`).
  - `no-new-privileges: true`.
  - Strictly confined volume mounts (`data/workspaces/{emp_id}` -> `/home/employee/workspace`).
  - Strict prohibition against mounting `/var/run/docker.sock` or host root filesystems.
  - VNC / noVNC desktop streaming for visible human and agent interaction.
- **Skill Verification**: Newly assigned skills are created with `verified=False`. Skills become verified only through empirical testing and independent verification receipts.

### 3. Real Laya Model Integration (System 1 Fast Decision Intelligence)
- Genuine neural model inference powered by official Laya checkpoints (`convaiinnovations/laya` ModernBERT weights).
- High-throughput System 1 inference for:
  - `predict_choice`: Multi-option classification and routing.
  - `predict_score`: Numerical quality and risk scoring.
  - `predict_noul`: Novelty, uncertainty, and learning urgency assessment.
- **Fallback & Safety**: If weights are missing, offline, or fail SHA256 integrity verification, gracefully falls back to deterministic heuristic rules (`ModelRuntime.FALLBACK`).
- **Policy Engine Sovereignty**: Laya is strictly an advisory fast cognitive filter; the independent ANCOS policy engine and Human Approval Gates retain final authority over all privileged or destructive operations.

### 4. Live Boss & Employee Intelligence (Multi-Tier LLM Gateway)
- Multi-tier routing: `reasoning` (planning, review, meetings), `coding` (software engineering, debugging), `fast` (status reporting, logging).
- Robust circuit breaking (`CircuitBreaker`) and exponential backoff retry (`retry_with_backoff`).
- Real-time USD cost tracking with automatic hard budget ceilings per task, project, and month (`CostTracker`).
- Transparent fallback between configured cloud LLM providers (Anthropic, OpenAI, Gemini) and offline simulated development mode when API keys are absent.

### 5. Strict GUI-Only Employee Execution & HumanSpeed Benchmark
- **Visual-First Execution**: Employees executing GUI tasks interact strictly through actual screenshots, pixel-based visual perception, visual grounding, and smooth motor actions (`GUIExecutionLoop`, `DualBrainCognitiveController`).
- **Cheating Prohibited**: Direct DOM injection, accessibility tree bypasses, Playwright/Selenium hooks, and hidden CLI shortcuts are strictly prohibited for GUI-assigned workflows.
- **ANCOS-HumanSpeed 4x Comparator**:
  - Compares ANCOS execution time against verified human medians: $\text{Speedup} = T_{\text{human}} / T_{\text{ancos}}$.
  - **Disqualification Rule**: If task output fails visual verification or triggers a safety violation, $\text{Speedup} = 0.0$. A failed task never counts as a fast success.

### 6. Production Backup, Disaster Recovery & Auditability
- **DisasterRecoveryManager**: Automated hot SQLite backup, asset packaging, SHA-256 component manifests, and clean-room full restoration with SQLite `PRAGMA integrity_check`.
- **Immutable Hash Chains**: Security audit trail with cryptographic hash chaining ensuring tamper-evident operational history.

### 7. Interactive Terminal AI Coder REPL (`ancos`)
- **Zero-Argument Launch**: Typing `ancos` or `python -m neural.cli` launches the frontier AI Coder REPL directly under employee identity `ANC-EMP-CODER`.
- **Autonomous Coding Loop**: AST repository exploration, file edit planning, live Rich syntax-highlighted diffs, PTY deadlock-guarded streaming test execution, and autonomous bug diagnosis & self-fixing loop until exit code 0.
- **Interactive Slash Commands**: `/diff` (unified diff), `/test` (pytest runner), `/evolve` (self-evolution), `/approve` (approval gates), `/status` (company health), `/exit`.
- **Autonomous Self-Evolution (`/evolve`)**: Profiles codebase bottlenecks, provisions isolated candidate worktrees, implements vectorized fast paths, benchmarks speedup (>= 2.0x), passes multi-gate verification, and merges with sovereign human approval.

---

## Testing & Verification

### Running Test Suites
```bash
# Full test suite (311 tests)
uv run pytest

# P0 Real Laya Model Neural Inference
uv run pytest tests/integration/test_p0_real_laya_model.py

# P0 Docker Zero-Trust & Broker Security
uv run pytest tests/integration/test_p0_docker_zero_trust.py

# Disaster Recovery & Hot Backup
uv run pytest tests/unit/test_disaster_recovery.py
```

### Dashboard Build
```bash
cd apps/dashboard
npm run build
```

---

## Operating Invariants & Prohibitions
1. **Never Bypass Policy Gates**: All destructive actions (container wipe, production deploy, external payments) require explicit approval or emergency halt checks.
2. **Never Fake Progress**: Verification requires ground-truth runtime receipts (passing pytest logs, valid exit codes, visual state changes).
3. **Never Hardcode Secrets**: Keep all API keys and secrets in environment variables or `.env`. Never commit credentials to git.
4. **Never Expose Unrestricted Docker Control**: Application containers must communicate only through `RestrictedDockerBroker`.
5. **Zero Sugar-Coating & Brutal Ground-Truth Honesty**: Strictly prohibit buzzwords, promotional fluff, and exaggerated capability claims. Directly communicate limitations, failures, simulated vs. live aspects, compute/token costs, and exact empirical evidence without excuses.

## Known Pitfalls

- **SQLite `NUMERIC` is stored as `REAL`.** Repeated `reserved_usd +/- amount` cycles do not round-trip exactly (e.g. `1.0 + 0.0128586 - 0.0128586` -> `0.9999999999999999`). Any budget comparison or decrement must tolerate ~1e-8 drift and clamp at zero; otherwise the non-negative CHECK constraint or a `>= amount` guard fails and surfaces as HTTP 500. See `BudgetRepository._decrement_reserved` and `_DRIFT_TOLERANCE`.
- **Never invoke tools by bare name in a subprocess.** The runtime venv is not on `PATH`, so `subprocess.run(["pytest", ...])` / `["python", ...]` raise `FileNotFoundError`, and `uv run ruff` inside a candidate worktree fails with "Failed to spawn: ruff". Use `sys.executable -m <module>` (or `python -m ruff`) so tests and the self-evolution Static Analysis Gate work in any environment. A tool-launch failure (ruff exit code 2) must fall through to the AST check, never be reported as a lint violation.
- **`TaskScheduler` must never fabricate a RUNNING row without an assignee.** In `dispatch_ready_tasks`, a ready task with no available employee stays `PENDING` and is tracked in `_active_task_ids`; `get_ready_tasks` excludes tasks in that set, so the DAG loop does not re-dispatch them (no livelock, no duplicate execution). A prior version set such tasks to `RUNNING` with `assigned_to=None`, which stranded them: `recover_stale_assignments` now also releases orphaned RUNNING/ASSIGNED rows whose `assigned_to` is NULL back to PENDING.
- **GUI execution runs on the employee container's X display, never on the core host.** `ContainerDesktopDriver` shells `scrot`/`xdotool` inside the container through the restricted broker. Two real-desktop traps: (1) `xdotool mousemove --sync` blocks forever when the pointer is already at the target (no motion event arrives) — never use `--sync`; (2) the core-api image must ship `tesseract-ocr` (see `docker/Dockerfile.core`), or postcondition verification raises `DesktopRuntimeUnavailable`.
- **Tesseract page-segmentation mode matters for desktop screenshots.** `--psm 11` (sparse text) fragments menu bars and window titles into unreadable noise; `--psm 6` (single uniform block) reads them cleanly. `TesseractOCREngine` defaults to psm 6 and emits both word-level boxes (pixel-precise single-label clicks) and line-level aggregates (multi-word phrase matches).
- **The keyboard focus gate fails closed on a real desktop.** A real X display has no focus to inherit, so a `KEY_TYPE` step without a prior click is blocked (`FOCUS_FAILED`). The step planner clicks the target field first, and `GuiWorkerEngine` reuses the last click coordinate for a coordinate-less `KEY_TYPE` step.
- **Visual grounding failure returns `(-1, -1)`, not an exception.** `VisualGrounder.ground_query` yields a low-confidence `(-1, -1)` target when nothing matches; dispatching that would be a blind click. `GuiWorkerEngine._ground` treats negative coordinates as a grounding failure and refuses to act.
- **New employee columns need both the ORM model and an Alembic migration, and the live dev DB must be upgraded.** `init_db` refuses to start when the DB is not at Alembic head (`expected='<head>'`). `data/neural_company.db` is git-ignored but is the running dev database; run `alembic upgrade head` against it after adding a migration.

