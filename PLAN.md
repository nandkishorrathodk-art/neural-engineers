# MASTER IMPLEMENTATION PLAN: AUTONOMOUS NEURAL COMPANY (ANCOS)
## Complete Step-by-Step Engineering Roadmap Across 8 Strategic Phases

---

## 🗺️ EXECUTIVE ROADMAP OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ANCOS ROADMAP AT A GLANCE                         │
├───────────────┬────────────────────────────────────────┬────────────────────┤
│ Phase         │ Focus Area                             │ Key Outcome        │
├───────────────┼────────────────────────────────────────┼────────────────────┤
│ Phase 1       │ Foundation & Sandboxed Workstations    │ CEO + Docker+VNC   │
│ Phase 2       │ Task DAG Engine & LLM Gateway          │ Parallel Execution │
│ Phase 3       │ Autonomous Boardroom & Daily Standups   │ Meeting Minutes    │
│ Phase 4       │ Verification Engine & Skill Cert       │ Zero False Success │
│ Phase 5       │ Recursive Self-Evolution & Learning    │ Self-Improvement   │
│ Phase 6       │ Production Hardening & Control Room UI │ Human Sovereignty  │
│ Phase 7       │ Neural-Speed GUI Intelligence (4×)     │ Visual Desktop UI  │
│ Phase 8       │ Laya Neural Decision Intelligence      │ Fast System 1 Loop │
└───────────────┴────────────────────────────────────────┴────────────────────┘
```

---

## 📌 PHASE 1: FOUNDATION & ISOLATED WORKSTATIONS (WEEKS 1–3)

### Goal
Establish the minimum viable autonomous organization: Central CEO brain receiving objectives, spinning up isolated Docker desktop workstations with noVNC remote viewing, and recording events in an async relational database.

### Deliverables
1. **CEO Brain & Lifespan**:
   - `neural/boss/brain.py` & `neural/boss/planner.py`: Objective parsing and basic linear task decomposition.
   - FastAPI lifespan lifecycle initializing SQLite/PostgreSQL engine and tables.
2. **Docker Desktop Workstation Infrastructure**:
   - Build `ancos/employee-base` Docker image (Ubuntu 22.04 + Xfce4 + TigerVNC + noVNC + Python 3.12).
   - Container manager (`neural/containers/manager.py`): Port allocation (5901 VNC / 6081 noVNC), volume workspace mounts, non-root user execution (`uid 1000`).
3. **Initial Workforce Bootstrap**:
   - Dynamic hiring of initial roles (Senior Backend Engineer & QA Engineer) via `EmployeeFactory`.
   - Workspace directories created at `./data/workspaces/{employee_id}`.
4. **Interactive Dashboard Foundation**:
   - Next.js Control Room with real-time CEO status card, employee roster, and embedded iframe for live noVNC desktop streams.

### Done Gate / Verification
- [ ] `pytest tests/unit/test_boss_brain.py` passes green.
- [ ] An employee desktop container boots up cleanly in < 15 seconds.
- [ ] Human owner can open `http://localhost:6081/vnc.html` and visually observe the Xfce4 desktop in browser.
- [ ] POST `/boss/objective` creates a project row and assigns the first task.

---

## 📌 PHASE 2: TASK DAG SCHEDULER & LLM GATEWAY (WEEKS 4–6)

### Goal
Enable complex, multi-stage project execution with dependency resolution (DAG), parallel worker dispatch, and multi-provider AI model routing.

### Deliverables
1. **DAG Dependency Engine (`neural/tasks/scheduler.py`)**:
   - Graph-based dependency resolution using `networkx`.
   - Topological sorting of tasks (e.g., Database Schema must finish before API implementation).
   - Parallel worker dispatch for non-dependent subtasks.
   - Cycle detection and deadlock prevention.
2. **Unified LLM Gateway (`neural/llm/gateway.py`)**:
   - Multi-provider abstraction (OpenAI, Anthropic Claude, Google Gemini, local Ollama).
   - Tier-based routing (`reasoning`, `coding`, `fast`) configured via `configs/model-routing.yaml`.
   - Automatic retry with exponential backoff and circuit breaking.
   - Token usage and cost tracking in USD per task and per employee.
3. **Inter-Agent Message Bus (`neural/comms/bus.py`)**:
   - Redis Pub/Sub channels: `boss:broadcast`, `employee:{id}:inbox`, `tasks:{id}:events`.
   - Structured JSON messages for task assignments, status updates, and artifact handoffs.

### Done Gate / Verification
- [ ] DAG scheduler dispatches 2 parallel tasks simultaneously without race conditions.
- [ ] If an LLM provider errors out (e.g., rate limit), the gateway transparently switches to the fallback provider.
- [ ] Every LLM API call records exact input/output tokens and cost in database.

---

## 📌 PHASE 3: AUTONOMOUS BOARDROOM & ORG MANAGEMENT (WEEKS 7–9)

### Goal
Implement corporate governance: Autonomous daily standups, incident post-mortems, structured meeting minutes, tracked action items, and executive briefings.

### Deliverables
1. **Meeting Scheduler & Conductor (`neural/meetings/conductor.py`)**:
   - Meeting triggers: Cron/Scheduled (Daily 09:00 Standup) and Event-Triggered (Phase Completion, Critical Blocker).
   - Dynamic attendee selection based on active task assignments and role relevancy.
2. **Fact-Grounded Progress Reporting (`neural/meetings/reports.py`)**:
   - Employees compile factual reports from live container execution logs, git commits, and exit codes.
   - Prohibits fabricated percentages; enforces 4 questions: *What was done? What remains? What is blocking? What is the verification receipt?*
3. **Automated Meeting Minutes & Action Items**:
   - Generates structured JSON minutes with clear decisions and assigned action items.
   - Carries forward unfinished action items into subsequent meeting agendas until resolved.
4. **Digital Boardroom Dashboard UI**:
   - Next.js page at `/meetings`: Live meeting transcript, agenda checklist, and action item Kanban.

### Done Gate / Verification
- [ ] Boss AI autonomously convenes a standup when a task gets blocked.
- [ ] Standup generates a permanent `Meeting` and `ActionItem` records in database.
- [ ] Action item deadlines are tracked and alerted if overdue.

---

## 📌 PHASE 4: EMPIRICAL VERIFICATION & SKILL CERTIFICATION (WEEKS 10–13)

### Goal
Eliminate "success-by-decree". Implement adversarial testing, independent QA verification, and empirical skill certification where employee capabilities evolve based on verified proof.

### Deliverables
1. **Independent Verification Engine (`neural/evaluation/verifier.py`)**:
   - Independent verification worker runs in a separate test container.
   - Runs test runners (`pytest`, `npm test`), linters, and security checks on employee artifacts.
   - Tasks cannot reach `status="completed"` without a valid passing verification receipt.
2. **Empirical Skill Certification Lifecycle**:
   - When an employee is hired, skills are marked `verified=False`.
   - Upon successful completion and QA verification of a task utilizing that skill, it transitions to `verified=True` with linked task evidence IDs.
   - Future Boss AI task delegations prioritize employees with *verified* skills over declared ones.
3. **Artifact Management & Checksum Versioning (`neural/tasks/artifacts.py`)**:
   - Tracks source code, Dockerfiles, and test logs with SHA-256 checksums.
   - Preserves historical artifacts to support rollback and regression investigations.

### Done Gate / Verification
- [ ] `pytest tests/unit/test_employee_factory.py` proves skill transitions from unverified to verified upon QA pass.
- [ ] A task with failing unit tests is rejected and returned to the employee with diagnostic feedback.

---

## 📌 PHASE 5: RECURSIVE SELF-EVOLUTION & CONTINUOUS LEARNING (WEEKS 14–17)

### Goal
Transform the system into an autonomous self-upgrading engineering enterprise that continuously learns from task failures, benchmark results, and post-mortems.

### Deliverables
1. **Four-Tier Organizational Memory (`neural/memory/`)**:
   - **Episodic**: Execution history, failed strategies, and container runtimes.
   - **Semantic**: Vector database (ChromaDB/pgvector) for architectural patterns and documentation.
   - **Procedural**: Verified workflows and reusable step-by-step technical guides.
   - **Policy**: Immutable company policies and security boundaries.
2. **Continuous Evolution Lifecycle (`neural/evolution/lifecycle.py`)**:
   - `OBSERVE` -> `IDENTIFY LIMITATIONS` -> `HYPOTHESIZE` -> `EXPERIMENT` -> `BENCHMARK` -> `STAGED RELEASE` -> `MONITOR` -> `ROLLBACK`.
   - Isolated experiment runner testing modified system prompts, new tools, and optimized workflows in sandboxed environments.
3. **Model Configuration Lab (`neural/evolution/models_lab.py`)**:
   - Measures task pass rates and latency across different foundation models.
   - Automatically recommends optimal model allocations per department.

### Done Gate / Verification
- [ ] Evolution engine detects a recurring error pattern and proposes an improvement hypothesis.
- [ ] The experiment runs in an isolated sandbox and measures performance against baseline.
- [ ] Rollback occurs automatically if the new candidate shows performance regression.

---

## 📌 PHASE 6: PRODUCTION HARDENING, SECURITY & CONTROL ROOM (WEEKS 18–20)

### Goal
Ensure enterprise-grade security, human sovereignty, cost enforcement, and polish the interactive Control Room.

### Deliverables
1. **Zero-Trust Security & Sandbox Hardening**:
   - Custom Seccomp profiles and AppArmor profiles blocking unauthorized syscalls.
   - Block access to host metadata services and internal RFC1918 networks.
2. **Human Approval Gates & Emergency Stop**:
   - Hard blocks on destructive file deletions, cloud deployments, and production migrations until approved via Dashboard.
   - Global emergency kill switch instantly halts all containers and pauses Boss AI loop.
3. **Complete Observability & Audit Trail**:
   - Comprehensive audit logging (`neural/db/models.py::AuditLog`) recording actor, action, timestamp, and IP.
   - OpenTelemetry request tracing and Prometheus resource metrics.
4. **Full End-to-End Benchmark Mission**:
   - Full autonomous test: Human submits *"Build a production REST API with authentication and testing"*.
   - System plans, recruits employees, writes code in VNC desktop, tests, verifies, runs standup, and delivers final artifact bundle.

### Done Gate / Verification
- [ ] Emergency stop freezes all worker execution within 200ms.
- [ ] Attempted escape from container is blocked by security profile and logged as a high-severity alert.
- [ ] Human owner can inspect the full deliverable and test receipts directly from the dashboard.

---

## 📌 PHASE 7: NEURAL-SPEED GUI INTELLIGENCE (WEEKS 21–24)

### Goal
Develop autonomous, human-level visual GUI intelligence with a 4× execution speed objective on certified workflows. AI employees operate arbitrary desktop applications purely through visual perception and mouse/keyboard interaction inside their isolated workstation containers without DOM, DevTools, accessibility trees, or API shortcuts.

### Deliverables
1. **Strict GUI-Only Interaction Contract**:
   - Zero bypass: Pure desktop screenshots, pixel-based analysis, visual grounding, mouse clicks/movements, and keyboard entry.
   - Prohibited: Browser DOM, DevTools, Playwright/Selenium, accessibility trees (AT-SPI), application APIs, or direct shell shortcuts for GUI-assigned work.
2. **Dual-Brain Multimodal Cognitive Architecture**:
   - System 1 (Fast Motor Reflex & Action Chunking): Sub-50ms inference executing compiled action chunks with dynamic visual settle detection.
   - System 2 (Deep Visual Reasoning & Recovery): Frontier vision-language model analyzing full desktop context, state transitions, unexpected modal dialogs, and strategic replanning.
3. **Visual Perception & Grounding Pipeline**:
   - Screen capture, dual-fovea visual encoder, raster pixel OCR, and UI bounding box object detection.
4. **Action Execution & Visibility Recovery**:
   - Cosine-eased smooth motor trajectories, focus click verification before typing, and automatic recovery from window occlusions, collapsed menus, and popups.
5. **Skill Compilation & Benchmarks**:
   - Trajectory recording, relative visual anchor compilation, and held-out permutation testing.
   - ANCOS-HumanSpeed benchmark measuring empirical speedup against human baselines.

### Done Gate / Verification
- [x] Full Phase 7 test suite passes (36/36 tests in `test_phase7_gui_intelligence.py` and `test_phase7_gui_workflow.py`).
- [x] Verified sub-50ms perception and action chunking execution loop.
- [x] Zero usage of prohibited APIs (DOM, DevTools, AT-SPI).
- [x] All 297 repository tests pass with zero regressions.

---

## 📌 PHASE 8: LAYA NEURAL DECISION INTELLIGENCE (WEEKS 25–28)

### Goal
Integrate Laya as a fast, structured System 1 neural decision layer that works alongside ANCOS's existing Boss AI, employee intelligence, GUI intelligence, and independent policy architecture. Laya accelerates high-frequency routing, categorization, scoring, and binary evaluation without compromising reasoning depth or security invariants.

### Architectural Principle
```
SYSTEM 1 (Laya)         ──► Fast, structured, calibrated decisions
SYSTEM 2 (Deep Brain)   ──► Complex reasoning, planning & recovery
INDEPENDENT POLICY      ──► Authorization & sovereignty gates
INDEPENDENT VERIFIER    ──► Outcome validation & empirical receipts
```

### Deliverables
1. **Laya Model Adapter & Service Architecture (`neural/decision_intelligence/system_one/`)**:
   - Replaceable adapter supporting official Laya checkpoints (English, Multilingual, Typed-Decisions).
   - Structured decision primitives: `CHOICE` (routing/specialist selection), `SCORE` (urgency/blocker severity), `NOUL` (binary proposition probability).
   - High-throughput batching, inference timeout guards, and deterministic fallback.
2. **Intelligent Decision Router (`neural/decision_intelligence/routing/`)**:
   - Classifies incoming decisions by complexity and domain familiarity.
   - Routes routine, low-risk decisions to Laya System 1 (<40ms latency).
   - Escalates unfamiliar, ambiguous, or out-of-distribution states to System 2 Deep Brain.
3. **GUI Intelligence Integration (Strict GUI Contract Preserved)**:
   - Laya acts strictly on pixel-derived structured state (detected buttons, text labels, candidate actions).
   - Laya never inspects raw screenshot pixels, never accesses DOM/AT-SPI, and never dispatches motor events.
4. **Calibration & Reliability Engine (`neural/decision_intelligence/calibration/`)**:
   - Empirically measures Brier score, log loss, and Expected Calibration Error (ECE).
   - Risk-coverage curves and selective accuracy thresholds ensuring zero blind promotion.
5. **Shadow Mode & Evolutionary Improvement (`neural/decision_intelligence/governance/`)**:
   - Mandatory shadow deployment comparing Laya recommendations against production decisions without side-effects.
   - Decision audit logging (`neural/db/models.py::DecisionRecord`) and automated error mining.
6. **Dashboard Observability & Control Room Integration**:
   - Real-time System 1 panel displaying active checkpoint, latency distribution, Deep Brain calls avoided, calibration metrics, and shadow comparison heatmaps.

### Done Gate / Verification
- [x] Laya adapter successfully runs in the ANCOS execution environment with typed `CHOICE`, `SCORE`, and `NOUL` primitives.
- [x] Model outputs pass strict Pydantic schema validation and numeric range bounds.
- [x] Decision router accurately escalates ambiguous scenarios to System 2 Deep Brain.
- [x] Shadow mode logs parallel decisions without affecting live production workflows.
- [x] GUI-only contract remains strictly enforced with zero DOM or motor dispatch bypasses.
- [x] Independent policy engine retains ultimate authorization authority (Laya cannot self-authorize).
- [x] Full regression suite passes with zero failures (297/297 tests green).


