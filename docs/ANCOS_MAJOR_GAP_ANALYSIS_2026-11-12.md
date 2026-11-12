# ANCOS Major Gap Analysis Report — 2026-11-12

## Acceptance status: NOT ACCEPTED for "dual-mode operational system"

ANCOS has substantial, real intelligence components (Laya System-1, GUI motor loop, verification, meetings, governance, evolution). What it does **not** yet have is the *workforce execution infrastructure* that turns those components into a coordinated, multi-mode operating company.

Concretely: **only one execution mode (`terminal`) is reachable end-to-end.** `gui`, `hybrid`, `authorized_tools`, and `research_reasoning` tasks are rejected before any project or employee is persisted. Terminal tasks themselves depend on a live employee desktop, which is by-design human-approval-gated and never auto-provisioned.

This report is a code-grounded gap inventory. Every claim cites `file:line`. Severity is my assessment: CRITICAL (blocks the stated product goal), HIGH (breaks a core workflow), MEDIUM (degrades reliability/correctness), LOW (polish/robustness).

Method: static code audit of `services/core/neural/**` (58,622 LOC) + live runtime probes against the running stack (`ancos-core-api` at `127.0.0.1:12000`). No subagents; read-only audit, no source modified.

---

## 0. Verified runtime facts (ground truth, not inference)

Live probes against the running deployment:

| Probe | Result | Evidence |
| --- | --- | --- |
| `POST /boss/objective` (terminal objective) | HTTP 200 | earlier turn; task `TASK-01-01-EA5D` COMPLETED with real `calc.py` + pytest receipt |
| `POST /boss/objective` (GUI-only objective) | **HTTP 503** `execution_mode_unavailable: No active task worker is registered for execution mode(s): gui` | live probe |
| Task `execution_mode` distribution in DB | `terminal` only (4 COMPLETED, 1 FAILED, 1 ASSIGNED, 1 PENDING) | live DB query |
| Running worker containers | `ancos-terminal-worker`, `ancos-terminal-verifier` only | `docker ps` |
| `POST /gui/plan` | HTTP 200 (real System-2 plan produced) | live probe |
| Stuck task example | `TASK-02-01-44C4` ASSIGNED, employee IDLE, container status `FAILED` | live DB query |

---

## 1. Execution architecture gaps

### G-1.1 — `gui`/`hybrid`/`authorized_tools` have no long-running worker consumer  **[CRITICAL]**

- Evidence: `docker-compose.yml:160-189` defines only `terminal-worker` (`command: python -m neural.workers.terminal`). `docker-compose.yml:190-221` defines only `terminal-verifier`. There is **no** `gui-worker`, `hybrid-worker`, or `tools-worker` service.
- The only worker claim loop declares terminal-only capability: `services/core/neural/workers/terminal.py:277` posts `{"execution_modes": ["terminal"]}`.
- Consequence: a GUI objective is rejected at plan time. `services/core/neural/boss/planner.py:262-268` raises `UnsupportedExecutionModeError` when a planned task's mode is not in `active_supported`, which defaults to `{TERMINAL, TERMINAL_ONLY}` (`planner.py:249-253`).
- Live proof: HTTP 503 `execution_mode_unavailable` (Section 0).
- Dependency: needs G-1.2 (mode-specific infra) and G-3.x (GUI execution wiring).
- Acceptance test: "GUI objective → actual desktop interaction" produces a visual receipt and independently verified outcome; `POST /boss/objective` with a GUI objective returns 200 and the task reaches COMPLETED via a GUI worker.

### G-1.2 — Terminal worker is coupled to a desktop workstation  **[CRITICAL]**

This is the architectural defect the reviewer flagged. Terminal-only work should need an *execution container*, not a *GUI desktop*.

- `services/core/neural/api/routes/worker.py:85-97` (claim): calls `container_manager.get_desktop_by_employee_id(...)`; if not `running`, releases the claim and returns `workstation_unavailable`.
- `services/core/neural/api/routes/worker.py:126-146` (start): same desktop liveness gate; raises 409 `Assigned employee workstation is not live.`
- `services/core/neural/api/routes/worker.py:359-369` (exec): again requires `workstation["status"] == "running"` before `execute_command`.
- **Deepest coupling:** `services/core/neural/db/repositories.py:573-577` — `claim_next_task` refuses any candidate whose employee has no `container` or `container.status != RUNNING`. So even before the HTTP layer, the data layer demands a running *desktop container* for *terminal* tasks.
- Consequence: a terminal task cannot run without a provisioned GUI desktop. Since provisioning is human-approval-gated (G-1.4), terminal work stalls whenever no desktop is live.
- Acceptance test: "Terminal-only objective → actual CLI execution" completes with **no** desktop container present (execution container only).

### G-1.3 — `UnifiedExecutionFabric` (the intended mode dispatcher) is dead code  **[HIGH]**

- Evidence: `services/core/neural/execution/fabric.py:53` defines `UnifiedExecutionFabric`, and `fabric.py:97-104` `get_supported_modes()` unconditionally advertises **all** modes as supported.
- But the class is never instantiated in `services/` — grep for `UnifiedExecutionFabric(` finds callers only in `tests/**` (`tests/integration/test_neuro3_unified_fabric.py`, `tests/unit/test_neuro3_execution_contracts.py`, `tests/acceptance/test_neuro_phase3_live_acceptance.py`).
- The real runtime path bypasses it entirely: `services/core/neural/api/routes/worker.py` hardcodes `ExecutionMode.is_terminal(...)` checks (`worker.py:117`, `worker.py:344`) and executes commands directly.
- Consequence: the declared "unified fabric across all 5 modes" does not govern production execution. `get_supported_modes()` would report `gui` as supported even though no GUI worker exists — a misleading capability signal.
- Dependency: A (recovery) should route through a real dispatcher, or the fabric should be deleted and the plan-time gate made the single source of truth.
- Acceptance test: `get_supported_modes()` reflects *actually registered* workers, and a test asserts a mode with no worker is reported unsupported.

### G-1.4 — `gui_only`/`terminal_only` are conflated into one normalized mode  **[MEDIUM]**

- Evidence: `services/core/neural/constants.py:27-42` `normalize()` maps `terminal`→`TERMINAL_ONLY` and `gui`→`GUI_ONLY`, collapsing 7 enum values into 5 canonical modes.
- `ALL_EXECUTION_MODES` (`constants.py:45-51`) lists only the 5 canonical modes.
- Consequence: the "constraint" semantics the reviewer described (`gui_only` = GUI with terminal fallback prohibited; `terminal_only` = terminal with GUI fallback prohibited) are not distinguishable from the plain modes at the type level. Today both `gui` and `gui_only` are simply "not supported".
- Recommended mapping (consistent with existing code): treat `gui_only`/`terminal_only` as *constraints* on `gui`/`terminal`, not as independent worker types.
- Acceptance test: "GUI-only objective → terminal fallback prohibited" and "Terminal-only objective → GUI fallback prohibited" both enforced and covered by tests.

### G-1.5 — Planner hardcodes terminal-only and warns the model away from other modes  **[MEDIUM]**

- Evidence: `services/core/neural/boss/planner.py:196-200` — prompt instructs: *"In this deployment, only terminal tasks have a long-running worker consumer… those modes will be rejected before any project or employee is persisted."*
- `services/core/neural/boss/brain.py:63` constructs `TaskPlanner(llm_gateway=...)` with `supported_modes=None`, so the gate default (terminal-only) always applies.
- Consequence: even if a GUI worker existed, the planner would still be told not to emit GUI tasks. This is a *configuration* gap that must change together with G-1.1.
- Acceptance test: with a GUI worker registered, a GUI-requiring objective yields GUI-mode tasks (not terminal substitutions).

---

## 2. Workforce intelligence gaps

### G-2.1 — `assigned_to` is never re-pointed on stale/infra failure  **[CRITICAL]**

- Evidence: `services/core/neural/db/repositories.py:520-611` `claim_next_task` iterates candidates and, when the assigned employee's container is not RUNNING, simply `continue`s (`repositories.py:576-577`). The task keeps its original `assigned_to`. No reassignment occurs.
- Live proof: `TASK-02-01-44C4` ASSIGNED to `ANC-EMP-19EF84AA26A6` (IDLE) whose container is `FAILED`, stuck indefinitely.
- Consequence: a single failed workstation permanently strands a task. No alternate employee can ever pick it up.
- Acceptance test: "Desktop failure → safe task recovery": task returns to PENDING and is claimable by another eligible employee.

### G-2.2 — Capability-aware routing exists only at dispatch, not at claim  **[HIGH]**

- Evidence: `services/core/neural/boss/delegator.py:52-91` `select_best_assignee` scores verified skills (good). `services/core/neural/tasks/scheduler.py:186-211` applies a `required_capabilities` intersection check at dispatch time.
- But `claim_next_task` (`repositories.py:578-586`) does a *skill-name* intersection only; it has **no notion of execution capability** (GUI/terminal/hybrid). A terminal worker will happily claim a `terminal` task whose employee was chosen for GUI work, and vice versa is impossible today.
- Consequence: capability profiles (the reviewer's §3 "employee has capabilities, not a fixed mode") are not represented anywhere. `Employee` has `skills` but no execution-capability field.
- Acceptance test: an employee without GUI capability is never assigned/claimable for a GUI task.

### G-2.3 — No automatic replacement for an unavailable employee  **[HIGH]**

- Evidence: `services/core/neural/tasks/scheduler.py:182` builds `available_employees = [e for e in employees if e.status == IDLE]`; `delegator.select_best_assignee` returns `None` when no idle employee qualifies (`delegator.py:63-69`).
- On `None`, the scheduler marks the task RUNNING **with no assignee** (`scheduler.py:212-214`) — a second defect (see G-6.2).
- Consequence: no reassignment/escalation for capacity gaps; tasks can be marked RUNNING with `assigned_to=None`.
- Acceptance test: when the assigned employee is unavailable, the task is reassigned to a qualified alternative or explicitly BLOCKED (never RUNNING-without-assignee).

---

## 3. GUI computer intelligence gaps

### G-3.1 — GUI motor loop is real but has no runtime consumer  **[CRITICAL]**

- Evidence: the package is substantial and genuine — `services/core/neural/gui_intelligence/` contains `action/execution_loop.py` (`GUIExecutionLoop`), perception (`screen_capture.py`, `visual_grounding.py`, `object_detection.py`, `pixel_ocr.py`), world model (`screen_state.py`, `visual_belief.py`, `window_state.py`, `transition_tracker.py`), recovery (`failure_detector.py`, `state_recovery.py`, `visibility_recovery.py`), safety (`risk_guard.py`, `gui_action_policy.py`), verification (`visual_verifier.py`, `task_completion.py`), and benchmarks (`osworld_runner.py`, `human_speed.py`).
- `GUIExecutionLoop` is instantiated only inside API routes (`services/core/neural/api/routes/gui_intelligence.py:88,98`) and the cognitive controller (`brain/cognitive_controller.py`), which itself is only used by the OSWorld benchmark runner (`benchmarks/osworld_runner.py:46`).
- Consequence: the GUI capability is a *library + demo endpoint*, not a workforce participant. No task in the DB has ever been executed via GUI.
- Dependency: G-1.1, G-1.2.
- Acceptance test: "GUI objective → actual desktop interaction" end-to-end, with screenshot artifacts persisted as evidence.

### G-3.2 — Screen capture is host-local, not bound to the employee desktop  **[HIGH]**

- Evidence: `services/core/neural/api/routes/gui_intelligence.py:100-120` `capture_screen` fetches the employee's workstation via `container_manager.get_desktop_by_employee_id(...)`. But `GUIExecutionLoop` itself (`execution_loop.py:41-55`) constructs a `ScreenCaptureEngine()` with no workstation binding — i.e., the motor loop captures the *host* screen, not the employee's VNC desktop.
- Consequence: even if a GUI worker were added, the observe-ground-act loop would not act on the employee's isolated desktop without a per-desktop capture/input backend.
- Acceptance test: GUI action receipts show the employee's desktop pixels (from the workstation container), not the host.

### G-3.3 — GUI failure/robustness scenarios are largely unimplemented  **[MEDIUM]**

- Evidence: `services/core/neural/gui_intelligence/recovery/` has `failure_detector.py`, `state_recovery.py`, `visibility_recovery.py` (real modules), but there is no runtime loop that invokes them during task execution, and no tests exercising: window focus loss, unexpected popup, login expiry, browser crash, app freeze, resolution change, coordinate drift, keyboard mismatch, upload/download failure, multi-window navigation, long-running tasks, unsaved-change recovery, or GUI prompt injection.
- Consequence: the "human-level computer use" claim (GOAL.md, PLAN Phase 7) is unproven beyond a synthetic plan endpoint.
- Acceptance test: recovery benchmarks (reviewer §5 P5) that inject these conditions and assert recovery.

---

## 4. Hybrid execution gaps

### G-4.1 — No hybrid execution path at all  **[HIGH]**

- Evidence: `ExecutionMode.HYBRID` exists (`constants.py:9`). It appears in `execution/fabric.py:406-413` (a verification contract) and `planner.py:570` (one code path emitting HYBRID). But no worker claims `hybrid`, and `worker.py:117` explicitly rejects non-terminal tasks for the only worker that exists.
- There is no checkpoint/handoff machinery between GUI and terminal phases; `resource_scheduler/checkpoint_manager.py` exists but is not wired to task execution.
- Consequence: "GUI → Terminal → GUI handoffs with preserved context" (reviewer §2C) is entirely unproven.
- Acceptance test: "Hybrid objective → both execution capabilities used where required", with one coherent execution history.

### G-4.2 — Execution history is per-attempt, not per-task-across-modes  **[MEDIUM]**

- Evidence: `TaskAttempt` records (`repositories.py:533-560`) are per worker attempt. `execution_receipt` is stored per submit (`worker.py:391-404`). There is no field/aggregate that chains GUI and terminal phases into one task timeline.
- Acceptance test: verifier receives combined GUI + terminal evidence for a hybrid task.

---

## 5. Resource & desktop management gaps

### G-5.1 — Four-slot desktop scheduler is not the runtime admission path  **[MEDIUM]**

- Evidence: `services/core/neural/resource_scheduler/hardware_profiler.py:43-46` defines `PROFILE_16GB` with `max_active_desktops=4`; `desktop_pool.py:65-118` implements `DesktopSlotPool`. But `DesktopSlotPool`/`DesktopResourceScheduler` are referenced only by `evaluation/cognitive_benchmark.py` and internally (`scheduler.py:44`). Actual provisioning admission is a **count check in the broker**: `services/core/neural/containers/broker_api.py:472-478` counts `ancos.managed=true` containers against `workstation_slot_limit()`.
- Consequence: there are *two* capacity mechanisms; the sophisticated slot pool with priority/standby is not what gates real provisioning. No priority allocation, no standby queue at runtime.
- Acceptance test: 5th desktop request is queued/denied per slot policy with priority ordering; terminal workers unaffected.

### G-5.2 — Employee identity is coupled to a running container  **[MEDIUM]**

- Evidence: `Employee.container_id` FK (`db/models.py`), and `claim_next_task` requires `employee.container.status == RUNNING` (`repositories.py:573-577`). Employee `status` is set IDLE/WORKING around container lifecycle (`worker.py:428-430`, `repositories.py:549-551`).
- Consequence: persistent employee identity vs ephemeral execution (reviewer §1 "Employee lifecycle") is not separated. An employee with no live container is effectively unusable.
- Acceptance test: an employee can be IDLE with no container and still be routed to a task that provisions/leases ephemeral execution.

---

## 6. Task execution & recovery gaps

### G-6.1 — Stale-assignment recovery is partial and conflated with code failure  **[CRITICAL]**

- Evidence: lease expiry *is* handled inside `claim_next_task` (`repositories.py:542-561`): an expired `leased`/`running` attempt is marked failed, and the task is retried (`task.retry_count += 1`) or FAILED if `retry_count >= max_retries`.
- But: (a) this path only triggers when a *worker* re-polls and considers the task; it does not fire for an employee whose *container is FAILED* (that path just `continue`s, G-2.1); (b) an infrastructure outage (dead workstation) increments `retry_count` and consumes the retry budget (`repositories.py:557`), contradicting the reviewer's requirement that infra failure not count against the task's code-failure retries.
- Acceptance test: "Desktop failure → safe task recovery" and "Worker restart → task lease and execution recovery", with `retry_count` unchanged by pure infra failures.

### G-6.2 — Task can be marked RUNNING with no assignee  **[HIGH]**

- Evidence: `services/core/neural/tasks/scheduler.py:212-214`:
  ```python
  else:
      # No employee available, mark assigned to keep slot ready or leave pending
      await self.task_repo.update_status(task.task_id, TaskStatus.RUNNING)
      task.status = TaskStatus.RUNNING
  ```
  `dispatch_ready_tasks` also consumes a concurrency slot for it (`scheduler.py:167-170` counts RUNNING/ASSIGNED as active).
- Consequence: a task with `assigned_to=None` sits RUNNING forever, invisible to `claim_next_task` (which requires `assigned_to IS NOT NULL`, `repositories.py:494`), and consumes a dispatch slot.
- Acceptance test: no task ever reaches RUNNING without an assignee; such a task stays PENDING or becomes BLOCKED.

### G-6.3 — Duplicate-execution prevention relies on fencing tokens (present) but requeue is not idempotent across modes  **[MEDIUM]**

- Evidence: fencing tokens are implemented (`repositories.py:611-...`, `worker.py:111-113`), which is good. However, requeue of a stale assignment (needed for A) has no idempotency key today, and there is no "attempt invalidation" wired from the dead fabric (`fabric.py` `_active_attempts`).
- Acceptance test: a requeued task is executed exactly once; a late-returning stale worker is rejected (409).

### G-6.4 — No automatic detection of dead workers  **[MEDIUM]**

- Evidence: `ServiceHeartbeatRepository` exists and `worker.py`/`terminal.py:293-305` send runtime heartbeats, but nothing consumes missed heartbeats to release leases or mark workers dead. Only the lease *expiry* path (G-6.1) reacts, and only opportunistically.
- Acceptance test: a killed worker's lease is reclaimed within one lease window and the task requeued.

---

## 7. Independent verification gaps

### G-7.1 — Terminal verification is genuinely independent (strength)  **[OK]**

- Evidence: separate `ancos-terminal-verifier` process (`docker-compose.yml:190-221`, `workers/verifier.py`) with its own token (`VERIFIER_API_TOKEN`), independent workspace manifest verification (`verification/workspace_manifest.py`), and test-plan validation (`verification/test_commands.py`). Proven live: `TASK-01-01-EA5D` produced an independent pytest receipt (8 passed).
- No action; keep.

### G-7.2 — No independent *visual* verification wired to tasks  **[HIGH]**

- Evidence: `gui_intelligence/verification/visual_verifier.py` (`IndependentVisualVerifier`) exists and is used by `cognitive_controller.py:76` and `osworld_runner.py:48`, but nothing in the task/verification pipeline invokes it for GUI tasks. `evaluation/verifier.py` (`IndependentDeliverableVerifier`) handles terminal evidence.
- Consequence: "GUI verification → real screenshots and outcome evidence" is unproven for tasks.
- Acceptance test: GUI task completion requires an independent visual verification receipt.

### G-7.3 — `TaskCompletionVerifier` is unwired  **[MEDIUM]**

- Evidence: `gui_intelligence/verification/task_completion.py` exists; grep for `TaskCompletionVerifier` outside its own definition returns **0** references in `services/`.
- Acceptance test: GUI task completion calls the completion verifier and records its receipt.

---

## 8. Company-level intelligence gaps

### G-8.1 — Meetings/governance are request-driven, not self-driven  **[MEDIUM]**

- Evidence: `api/main.py` lifespan starts only the outbox worker and the resource-lease reaper (`main.py:57-74`). There is **no** background loop for daily standups, department meetings, weekly reviews, or project health monitoring. Meetings exist as endpoints (`routes/meetings.py:265-281` `trigger/standup`, `:391` convene, `:524` conduct_standup) and governance via `routes/governance.py:154` — all human/API triggered.
- Consequence: "Company apne aap standups/reviews kare" is not automatic; the organization does not self-operate.
- Acceptance test: a scheduled standup/review is created automatically on a cadence and produces minutes without a manual trigger.

### G-8.2 — Company-wide learning loop is not connected to execution outcomes  **[MEDIUM]**

- Evidence: `evolution/gap_analysis.py`, `meta_learning.py`, `curriculum.py` exist; `routes/evolution.py` exposes them, but there is no automatic trigger from task failure patterns to learning/gap analysis at runtime.
- Acceptance test: repeated failure class → automatic gap-analysis record and (gated) skill/tool proposal.

---

## 9. Self-evolution safety gaps

### G-9.1 — Evolution has strong gating (strength) but is not automatic  **[MEDIUM]**

- Evidence: `evolution/` implements `multi_gate_verifier.py`, `promotion_manager.py`, `rollback_manager.py`, `candidate_staging.py`; `routes/evolution.py:165` builds `SelfEvolutionEngine`. Destructive op types include `SELF_EVOLUTION_PROMOTION` (`constants.py:230`) — human-sovereignty gating exists.
- Gap: no automatic failure-pattern → candidate → benchmark → gated-promotion pipeline; it is manual/API-driven.
- Acceptance test: `/evolve` self-evolution path profiles a real bottleneck, produces a ≥2× candidate, passes multi-gate verification, and requires approval before merge.

---

## 10. Cross-cutting gaps

### G-10.1 — Cost intelligence exists but is per-scope, not per-task/project roll-up  **[LOW]**

- Evidence: `llm/budget.py` + `CostTracker` and the monthly-budget drift fix (this session, commit `13a3d12`). Per-task model receipts are stored in `execution_receipt.model` (`worker.py`/`terminal.py:183-192`). No dashboard roll-up of per-project cost was verified.
- Acceptance test: per-task and per-project USD roll-up exposed and accurate.

### G-10.2 — Observability: telemetry/audit exist; end-to-end trace timeline unproven  **[MEDIUM]**

- Evidence: `observability/{audit,telemetry,metrics}.py` and `routes` for telemetry exist (`/observability/telemetry` returned 200 live). Audit hash-chaining per AGENTS.md. But no single end-to-end execution timeline (plan → dispatch → claim → exec → verify → complete) is assembled.
- Acceptance test: one trace id yields the full execution timeline for a task.

### G-10.3 — Disaster recovery exists but desktop/workspace recovery unproven  **[MEDIUM]**

- Evidence: `ops/backup.py` (DisasterRecoveryManager) with SQLite hot backup + manifest + integrity check per AGENTS.md. Desktop container recovery (re-attach/re-create a FAILED workstation) is not implemented — relevant to the stuck `FAILED` container.
- Acceptance test: restore brings back DB **and** re-provisions a working desktop for an employee.

### G-10.4 — Testing realism: strong unit/integration, weak real-world acceptance  **[MEDIUM]**

- Evidence: 118 test files; full suite 816 passed / 11 skipped. But acceptance tests lean on synthetic fixtures; the only true end-to-end GUI/browser acceptance (OSWorld) is a benchmark runner not run in CI. Real-world GUI acceptance tests do not exist.
- Acceptance test: the reviewer's final acceptance matrix runs in CI against a real desktop.

---

## Priority-ordered gap closure plan

Aligned with the reviewer's roadmap. Each item lists the gap IDs it closes.

### P0 — Execution architecture correction (Milestone A)
- G-1.2: remove desktop dependency from terminal-only execution (data layer `repositories.py:573-577`, `worker.py:85/126/359`).
- G-2.1 + G-6.1: durable requeue + capability-aware reassignment on FAILED/absent workstation; infra failure does not consume retry budget.
- G-6.2: never mark RUNNING without an assignee.
- G-1.4: treat `gui_only`/`terminal_only` as constraints.
- Keep human provisioning approval (G-1.4 security).
- Acceptance tests: Desktop failure → safe recovery; Terminal-only objective with no desktop.

### P1 — Real GUI worker (Milestone B)
- G-1.1 + G-3.1: persistent `gui-worker` service claiming `gui` capability.
- G-3.2: bind capture/input to the employee's VNC desktop.
- G-7.2 + G-7.3: independent visual verification wired into task completion.
- G-1.5: planner allowed to emit GUI tasks when a GUI worker is registered.
- Acceptance tests: GUI objective → real desktop interaction; GUI verification with screenshots.

### P2 — True hybrid execution
- G-4.1 + G-4.2: GUI/terminal switching, shared workspace, checkpointed handoffs, continuous history.
- Acceptance test: Hybrid objective uses both capabilities.

### P3 — Workforce & desktop intelligence
- G-5.1: make the four-slot pool the real admission path; priority + standby.
- G-5.2 + G-2.3: persistent identity vs ephemeral execution; automatic replacement.
- Acceptance test: 5th desktop queued by policy; reassignment on unavailability.

### P4 — Reliability & safety
- G-6.3 + G-6.4: duplicate-execution protection, dead-worker detection.
- G-3.3: GUI failure recovery.
- G-7.2: multimodal independent verification.
- Acceptance tests: Worker restart recovery; GUI failure recovery.

### P5 — Human-level computer benchmarking
- G-3.3 + G-10.4: complex real GUI tasks, long-horizon, recovery benchmarks, human baseline, 4× target.
- Acceptance test: ANCOS-HumanSpeed 4× comparator with real tasks.

---

## Final acceptance matrix (do not declare dual-mode complete without these)

| Test | Expected result | Currently |
| --- | --- | --- |
| Terminal objective | Actual CLI execution | PASS (with live desktop) |
| GUI objective | Actual desktop interaction | **FAIL** (503 at plan time) |
| GUI-only objective | Terminal fallback prohibited | **FAIL** (mode unsupported) |
| Terminal-only objective | GUI fallback prohibited | Partial (desktop-coupled) |
| Hybrid objective | Both capabilities used | **FAIL** (no hybrid path) |
| Desktop failure | Safe task recovery | **FAIL** (task stuck) |
| Worker restart | Task lease + execution recovery | Partial (lease expiry only) |
| GUI verification | Real screenshots + outcome evidence | **FAIL** (unwired) |

---

## Conclusion

The intelligence is largely present; the *execution workforce* is not. The single highest-leverage correction is **P0/A**: decouple terminal execution from the desktop, and make stale-assignment recovery durable and capability-aware. That converts ANCOS from "fragile single-mode" to "reliable single-mode" and is a prerequisite for a trustworthy GUI worker. **P1/B** then makes it genuinely dual-mode.

Neither should be called done without the acceptance matrix above passing on real infrastructure.
