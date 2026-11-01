# ANCOS — Autonomous Neural Company Operating System
## Complete System Architecture

---

## 1. VISION SUMMARY

ANCOS is a **self-managing, self-evolving, multi-agent AI engineering organization**.

**Core capabilities:**
- One Boss AI / CEO orchestrates everything
- Dynamic AI employee creation with verified skill profiles
- Isolated Docker desktops with VNC for every employee
- Autonomous meetings, boardroom, and company management
- Persistent organizational memory (episodic + semantic + procedural)
- Recursive self-evolution and autonomous learning
- Independent verification — nothing is "done" until proven
- Human owner retains ultimate authority with emergency stop

**This is NOT:** a chatbot, a fixed workflow tool, or a static agent collection.

---

## 2. HIGH-LEVEL ARCHITECTURE

```
┌──────────────────────────────────────────────────────────────────┐
│                    👤 HUMAN OWNER LAYER                          │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Control Room     │  │ Boardroom /  │  │ noVNC Desktop      │  │
│  │ Dashboard        │  │ Meeting Room │  │ Gateway            │  │
│  └────────┬─────────┘  └──────┬───────┘  └────────┬───────────┘  │
│           │                   │                    │              │
├───────────┼───────────────────┼────────────────────┼──────────────┤
│           ▼                   ▼                    ▼              │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              🔌 API GATEWAY (FastAPI + WebSocket)           │  │
│  └────────────────────────────┬───────────────────────────────┘  │
│                               │                                  │
├───────────────────────────────┼──────────────────────────────────┤
│                    🧠 INTELLIGENCE LAYER                         │
│  ┌────────────┐  ┌───────────┴──────┐  ┌──────────────────┐     │
│  │ Boss AI /   │  │ Task Planner &   │  │ Meeting &        │     │
│  │ CEO Brain   │◄─┤ Scheduler (DAG)  │  │ Boardroom Engine │     │
│  └──────┬──────┘  └─────────────────┘  └──────────────────┘     │
│         │                                                        │
│  ┌──────┴──────┐  ┌─────────────────┐  ┌──────────────────┐     │
│  │ Employee     │  │ Verification &  │  │ Self-Evolution   │     │
│  │ Recruiter    │  │ QA Engine       │  │ Engine           │     │
│  └──────┬──────┘  └─────────────────┘  └──────────────────┘     │
│         │                                                        │
├─────────┼────────────────────────────────────────────────────────┤
│         ▼         🔧 INFRASTRUCTURE LAYER                        │
│  ┌─────────────┐  ┌─────────────────┐  ┌──────────────────┐     │
│  │ Docker       │  │ LLM Model       │  │ Organizational   │     │
│  │ Container    │  │ Gateway         │  │ Memory Store     │     │
│  │ Manager      │  │ (Multi-Provider)│  │ (Vector+Graph)   │     │
│  └──────┬──────┘  └─────────────────┘  └──────────────────┘     │
│         │                                                        │
│  ┌──────┴──────┐  ┌─────────────────┐  ┌──────────────────┐     │
│  │ Security &   │  │ Message Bus     │  │ Artifact &       │     │
│  │ Policy       │  │ (Redis Pub/Sub) │  │ File Manager     │     │
│  │ Engine       │  │                 │  │                  │     │
│  └─────────────┘  └─────────────────┘  └──────────────────┘     │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                    🐳 EMPLOYEE RUNTIME LAYER                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Employee  │  │ Employee  │  │ Employee  │  │ Employee  │       │
│  │ Container │  │ Container │  │ Container │  │ Container │       │
│  │ 🖥️ VNC   │  │ 🖥️ VNC   │  │ 🖥️ VNC   │  │ 🖥️ VNC   │       │
│  │ Alex      │  │ Sarah     │  │ Dev       │  │ ...       │       │
│  │ Backend   │  │ Frontend  │  │ QA        │  │ Dynamic   │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
└──────────────────────────────────────────────────────────────────┘
```

---

## 3. TECHNOLOGY STACK

| Layer | Technology | Why |
|-------|-----------|-----|
| Backend Language | Python 3.12+ | AI ecosystem, async, rapid dev |
| Package Manager | `uv` | Fast, lockfile-based |
| API Framework | FastAPI | Async, WebSocket, OpenAPI docs |
| Task Queue | Redis + `arq` | Async jobs, priorities, retries |
| Primary DB | PostgreSQL | Structured data, JSONB, reliable |
| Fallback DB | SQLite | Zero-config dev/testing |
| Vector Store | pgvector (or ChromaDB) | Semantic search, embeddings |
| Message Bus | Redis Pub/Sub | Real-time inter-agent comms |
| Containers | Docker SDK for Python | Programmatic lifecycle mgmt |
| Desktop | Xfce4 in Docker | Lightweight Linux desktop |
| Remote Desktop | TigerVNC + noVNC | Browser-accessible VNC |
| Dashboard | Next.js 14 (App Router) | Server components, real-time UI |
| Dashboard UI | Tailwind + shadcn/ui | Consistent, accessible |
| LLM Gateway | LiteLLM / custom router | Multi-provider model routing |
| ORM | SQLAlchemy 2.0 (async) | Type-safe DB access |
| Migrations | Alembic | Schema versioning |
| Testing | pytest + playwright | Backend + E2E |

---

## 4. MODULE BREAKDOWN (17 Modules)

### Module 1: API Gateway (`neural/api/`)
- FastAPI application factory with CORS, lifespan hooks
- REST routes for Boss, Projects, Tasks, Employees, Containers, VNC, Memory, Artifacts, Approvals, Meetings, Metrics
- WebSocket routes for live logs, progress events, meeting streams
- Auth middleware (JWT), rate limiting, audit logging
- Pydantic request/response schemas

### Module 2: Boss AI / CEO Brain (`neural/boss/`)
- **brain.py** — Main orchestration loop: receive objective → analyze → plan → delegate → monitor → verify → report
- **reasoning.py** — Goal analysis, complexity assessment, feasibility, risk identification
- **planner.py** — Hierarchical decomposition: Project → Phases → Tasks → Subtasks; dependency DAG; parallel vs sequential
- **delegator.py** — Skill-matching task assignment, load balancing, priority scheduling
- **monitor.py** — Real-time progress tracking, timeout detection, stuck-task intervention
- **verifier.py** — Acceptance criteria checking, quality gates, cross-employee consistency
- **recovery.py** — Retry strategies, task reassignment, escalation to human, checkpoint restore
- **decision.py** — Autonomous decision-making within authority boundaries
- **state.py** — Boss state machine (idle → planning → executing → reviewing → reporting)
- **prompts.py** — Boss system prompts and templates

### Module 3: Employee System (`neural/employees/`)
- **registry.py** — Employee catalog, skill index, availability tracker, search
- **factory.py** — Dynamic creation: role def → system prompt → toolset → container → full agent config
- **agent.py** — Employee agent runtime loop: receive task → reason → use tools → report
- **lifecycle.py** — Hiring → Assignment → Suspension → Resume → Termination → Reuse
- **roles.py** — Built-in role definitions (SWE, Frontend, Backend, QA, DevOps, Security, Research, Writer, PM, ML Engineer, DB Engineer, etc.)
- **skills.py** — Skill definitions, proficiency levels, verification status
- **profile.py** — Rich employee profiles with personality, communication style, learning history
- **performance.py** — Performance tracking, scoring, skill verification records
- **prompts.py** — Employee prompt templates

**Employee Profile Model:**
```
Employee:
  identity:
    employee_id: "ANC-EMP-0042"
    name: "Alex Morgan"
    title: "Senior Backend Engineer"
    department: "Engineering"
    manager: "Boss AI"
    personality: "methodical, detail-oriented"
    communication_style: "concise, technical"
  
  skills:
    - name: "Python"
      level: "expert"
      verified: true
      verified_at: "2025-01-15"
      evidence: ["TASK-0012", "TASK-0018"]
    - name: "FastAPI"
      level: "advanced"
      verified: true
      evidence: ["TASK-0012"]
    - name: "PostgreSQL"
      level: "intermediate"
      verified: false
  
  capabilities:
    - "Backend development"
    - "Database architecture"
    - "API development"
    - "Performance optimization"
  
  resources:
    model_config: "claude-sonnet-4-20250514"
    system_prompt: "..."
    approved_tools: ["terminal", "code_editor", "git", "browser", "file_ops"]
    container_id: "docker-abc123"
    vnc_port: 5901
    workspace: "/workspace/projects"
  
  performance:
    tasks_completed: 24
    tasks_failed: 2
    quality_score: 0.92
    avg_completion_time: "2h 15m"
    verified_skills: ["Python", "FastAPI"]
    learning_history: [...]
    improvement_trajectory: "improving"
  
  status: "working"  # idle | working | suspended | terminated
```

**Skill Verification Flow:**
```
Boss assigns skill → Employee executes practical task → 
QA independently tests output → Success? → 
Skill marked "verified" with evidence links → 
Future assignments use verified skills for matching
```

### Module 4: Task & Project Engine (`neural/tasks/`)
- **models.py** — Task, Project, Milestone data models with full lifecycle fields
- **project.py** — Project workspace management, milestone tracking, progress aggregation
- **scheduler.py** — DAG-based execution order, parallel dispatch, priority queue, concurrency limits
- **executor.py** — Task dispatch to employee, monitoring, result collection, timeout handling
- **dependencies.py** — DAG construction, topological sort, cycle detection, critical path
- **artifacts.py** — Input/output artifact tracking, versioning, cross-task sharing

**Task Model:**
```
Task:
  task_id: "TASK-0042"
  project_id: "PROJ-001"
  parent_task_id: null  # for subtasks
  title: "Implement user authentication API"
  objective: "..."
  scope: "..."
  assigned_to: "ANC-EMP-0042"
  dependencies: ["TASK-0040", "TASK-0041"]
  required_capabilities: ["Python", "FastAPI", "JWT"]
  acceptance_criteria: "..."
  priority: "high"
  status: "running"
  resource_budget: {max_tokens: 100000, max_time: "4h", max_cost_usd: 5.0}
  verification_result: null
  retry_count: 0
  max_retries: 3
```

### Module 5: Meeting & Boardroom Engine (`neural/meetings/`)
- **scheduler.py** — Autonomous meeting scheduling (daily standups, project reviews, incident reviews, etc.)
- **conductor.py** — Meeting orchestration: set agenda → invite participants → run discussion → decisions → action items
- **participants.py** — Participant selection based on roles, assignments, expertise
- **reports.py** — Employee progress reports grounded in actual task states and evidence
- **minutes.py** — Structured meeting minutes generation with decisions, action items, deadlines
- **actions.py** — Action item tracking until completion, follow-up in subsequent meetings
- **briefings.py** — Executive briefings for human owner (company progress, risks, costs, achievements)
- **models.py** — Meeting, Agenda, MinuteEntry, ActionItem, Decision data models

**Meeting Types:**
```
- DAILY_STANDUP           — Every morning, all active employees
- PROJECT_REVIEW          — Per-project progress check
- DEPARTMENT_MEETING      — Department-specific discussions
- ARCHITECTURE_REVIEW     — Technical design decisions
- INCIDENT_REVIEW         — Post-failure analysis
- PERFORMANCE_REVIEW      — Employee evaluation
- HIRING_PLANNING         — Workforce needs assessment
- EVOLUTION_STRATEGY      — Self-improvement planning
- EXECUTIVE_BRIEFING      — Boss → Human owner report
- AD_HOC                  — Triggered by events/blockers
```

**Meeting Minutes Model:**
```
MeetingMinutes:
  meeting_id: "MTG-0015"
  type: "PROJECT_REVIEW"
  date: "2025-01-15T10:00:00Z"
  duration: "45min"
  participants: ["Boss AI", "ANC-EMP-0042", "ANC-EMP-0043"]
  agenda: [...]
  progress_reports: [...]
  discussions: [...]
  decisions: [{decision, rationale, decided_by}]
  problems_identified: [...]
  action_items: [{description, assignee, deadline, status}]
  follow_up_required: true
  next_meeting: "2025-01-17T10:00:00Z"
```

### Module 6: Docker & Container Infrastructure (`neural/containers/`)
- **manager.py** — Create, start, stop, restart, remove, inspect containers
- **templates.py** — Container templates per role (base image + role-specific tools)
- **network.py** — Isolated bridge networks, inter-container rules, internet ACLs
- **resources.py** — CPU/memory/disk/process/time limits and enforcement
- **security.py** — No privileged mode, no docker.sock, non-root user, seccomp profiles
- **vnc.py** — VNC server config, port allocation, auth tokens, noVNC proxy, screenshots

### Module 7: LLM Model Gateway (`neural/llm/`)
- **gateway.py** — Central router: task type → model selection, load balancing, fallback chains
- **providers.py** — Adapters for OpenAI, Anthropic, Google, Ollama, vLLM
- **cost.py** — Per-request metering, per-employee aggregation, budget enforcement, alerts
- **retry.py** — Retry, exponential backoff, circuit breaker per provider
- **optimizer.py** — Model evaluation, configuration experiments, selection recommendations

### Module 8: Inter-Agent Communication (`neural/comms/`)
- **bus.py** — Redis pub/sub message bus with typed routing and delivery guarantees
- **messages.py** — TaskAssignment, TaskResult, StatusUpdate, HelpRequest, MeetingInvite, etc.
- **channels.py** — Boss↔Employee, Employee↔Employee (supervised), Broadcast channels
- **events.py** — Real-time event stream for dashboard subscriptions

### Module 9: Organizational Memory (`neural/memory/`)
- **store.py** — Unified memory interface: store/retrieve/search/update with source attribution
- **graph.py** — Knowledge graph (entities + relations): Projects, Employees, Skills, Decisions
- **vector.py** — Vector/semantic memory: embeddings, similarity search, hybrid retrieval
- **episodic.py** — Episode memory: what was tried, what worked/failed, task-level experiences
- **procedural.py** — How-to knowledge: verified workflows, reusable procedures, best practices
- **semantic.py** — Domain knowledge: technical concepts, architecture patterns, API knowledge
- **employee_memory.py** — Per-employee learning memory: personal experiences, skill acquisition
- **policies.py** — Immutable company rules, security policies, human-controlled constraints

### Module 10: Tools & Capabilities (`neural/tools/`)
- **registry.py** — Tool registration with schema, permission-gated access, per-role allowlists
- **base.py** — Base tool interface (execute, validate, audit)
- **builtin/** — terminal, file_ops, browser, code_editor, git, search, screenshot, http
- **custom/** — Employee-created tools with sandboxed loading and approval flow

### Module 11: Security & Permissions (`neural/security/`)
- **policy.py** — Action → permission check, human-approval gates for irreversible actions
- **auth.py** — JWT for dashboard, VNC auth tokens, inter-service auth
- **secrets.py** — Encrypted credential store, per-employee injection, rotation
- **audit.py** — Every action logged, tamper-resistant, searchable audit trail
- **sandbox.py** — Tool permission checks, network ACLs, filesystem restrictions
- **approvals.py** — Human approval queue, approval/rejection handling, timeout escalation

**Human Authorization Required For:**
- Deleting important data
- Modifying production infrastructure
- Publishing/deploying externally
- External communications
- Spending money
- Accessing sensitive accounts
- Changing security policies
- Self-evolution changes to protected boundaries

### Module 12: Verification & Evaluation (`neural/evaluation/`)
- **verifier.py** — Independent result verification: acceptance tests, code quality, security scans
- **metrics.py** — Task completion rate, error rate, cost per task, execution time, employee reliability
- **reporter.py** — Project reports, employee performance reports, cost analysis
- **benchmarks.py** — Standard benchmarks, A/B comparison, regression detection

### Module 13: Self-Evolution Engine (`neural/evolution/`)
- **analyzer.py** — Identify recurring failures, bottlenecks, missing capabilities
- **proposer.py** — Generate improvement hypotheses: prompts, tools, skills, workflows
- **experimenter.py** — Run sandboxed experiments, A/B test new approaches
- **trainer.py** — Model evaluation, fine-tuning orchestration, dataset generation from experiences
- **lifecycle.py** — OBSERVE → IDENTIFY → HYPOTHESIZE → EXPERIMENT → BUILD → TEST → BENCHMARK → COMPARE → SELECT → DEPLOY → MONITOR → ROLLBACK
- **versions.py** — Version management for prompts, tools, configs; rollback capability
- **models_lab.py** — Model experimentation: evaluate foundation models, create specialized configs, parameter-efficient fine-tuning

**Evolution Cycle:**
```
OBSERVE company performance
    ↓
IDENTIFY limitations & gaps
    ↓
GENERATE improvement hypotheses
    ↓
DESIGN experiments
    ↓
BUILD improved versions (isolated)
    ↓
TEST & BENCHMARK
    ↓
COMPARE with existing baseline
    ↓
SELECT verified improvements
    ↓
DEPLOY through staged release (human approval for protected changes)
    ↓
MONITOR real performance
    ↓
RETAIN or ROLLBACK
    ↓
REPEAT
```

### Module 14: Database (`neural/db/`)
- **engine.py** — Async SQLAlchemy engine with connection pooling
- **models.py** — All ORM models (see schema below)
- **repositories.py** — Data access layer (repository pattern)
- **migrations/** — Alembic migration scripts

### Module 15: Observability (`neural/observability/`)
- **dashboard_data.py** — Aggregated data for Control Room (boss status, employees, tasks, costs)
- **logging.py** — Centralized structured logging (structlog)
- **tracing.py** — Request tracing (OpenTelemetry)
- **alerts.py** — Alert rules and notifications

### Module 16: Employee Workforce Evolution (`neural/workforce/`)
- **evaluator.py** — Continuous workforce evaluation: who's underperforming, who needs new skills
- **trainer.py** — Employee skill training: assign learning tasks, verify results
- **reorganizer.py** — Department restructuring, role changes, promotions
- **planner.py** — Hiring needs assessment based on upcoming project requirements

### Module 17: Config & Constants (`neural/config.py`, `neural/constants.py`)
- Pydantic BaseSettings with environment variable loading
- All enums, status codes, resource limits, defaults

---

## 5. DATABASE SCHEMA

### Core Tables

```
┌─────────────────┐     ┌──────────────────┐     ┌────────────────┐
│ PROJECTS        │     │ TASKS            │     │ TASK_DEPS      │
├─────────────────┤     ├──────────────────┤     ├────────────────┤
│ id (PK)         │◄────│ project_id (FK)  │     │ task_id (FK)   │
│ name            │     │ id (PK)          │◄────│ depends_on (FK)│
│ objective       │     │ task_id (UK)     │     │ type           │
│ status          │     │ parent_task_id   │     └────────────────┘
│ execution_plan  │     │ assigned_to (FK) │
│ created_at      │     │ title            │     ┌────────────────┐
│ updated_at      │     │ objective        │     │ TASK_RESULTS   │
└─────────────────┘     │ scope            │     ├────────────────┤
                        │ acceptance       │     │ id (PK)        │
┌─────────────────┐     │ required_skills  │     │ task_id (FK)   │
│ EMPLOYEES       │     │ priority         │     │ summary        │
├─────────────────┤     │ status           │     │ output_data    │
│ id (PK)         │     │ resource_budget  │     │ artifacts      │
│ employee_id (UK)│     │ verification     │     │ verified       │
│ name            │     │ retry_count      │     │ verified_notes │
│ title           │     │ max_retries      │     │ submitted_at   │
│ department      │     │ deadline         │     └────────────────┘
│ personality     │     │ started_at       │
│ comm_style      │     │ completed_at     │     ┌────────────────┐
│ skills (JSON)   │     └──────────────────┘     │ ARTIFACTS      │
│ capabilities    │                               ├────────────────┤
│ system_prompt   │     ┌──────────────────┐     │ id (PK)        │
│ model_config    │     │ CONTAINERS       │     │ project_id (FK)│
│ approved_tools  │     ├──────────────────┤     │ task_id (FK)   │
│ container_id(FK)│────▶│ id (PK)          │     │ name           │
│ status          │     │ docker_id (UK)   │     │ type           │
│ perf_summary    │     │ image            │     │ path           │
│ hired_at        │     │ status           │     │ checksum       │
└─────────────────┘     │ vnc_port         │     │ version        │
                        │ vnc_pass_hash    │     │ created_at     │
┌─────────────────┐     │ resource_limits  │     └────────────────┘
│ EMPLOYEE_SKILLS │     │ network_config   │
├─────────────────┤     │ started_at       │     ┌────────────────┐
│ id (PK)         │     └──────────────────┘     │ APPROVALS      │
│ employee_id(FK) │                               ├────────────────┤
│ skill_name      │     ┌──────────────────┐     │ id (PK)        │
│ level           │     │ MEETINGS         │     │ task_id (FK)   │
│ verified (bool) │     ├──────────────────┤     │ action_type    │
│ verified_at     │     │ id (PK)          │     │ description    │
│ evidence_tasks  │     │ meeting_id (UK)  │     │ justification  │
│ updated_at      │     │ type             │     │ status         │
└─────────────────┘     │ status           │     │ decided_by     │
                        │ scheduled_at     │     │ requested_at   │
┌─────────────────┐     │ started_at       │     │ decided_at     │
│ PERFORMANCE     │     │ ended_at         │     └────────────────┘
├─────────────────┤     │ participants     │
│ id (PK)         │     │ agenda           │     ┌────────────────┐
│ employee_id(FK) │     │ minutes (JSON)   │     │ MEMORY_ENTRIES │
│ task_id (FK)    │     │ decisions (JSON) │     ├────────────────┤
│ completion_time │     │ action_items     │     │ id (PK)        │
│ retry_count     │     └──────────────────┘     │ category       │
│ quality_score   │                               │ source         │
│ cost_tokens     │     ┌──────────────────┐     │ content        │
│ cost_usd        │     │ ACTION_ITEMS     │     │ metadata       │
│ outcome         │     ├──────────────────┤     │ embedding      │
│ recorded_at     │     │ id (PK)          │     │ confidence     │
└─────────────────┘     │ meeting_id (FK)  │     │ created_at     │
                        │ description      │     └────────────────┘
┌─────────────────┐     │ assignee (FK)    │
│ AUDIT_LOG       │     │ deadline         │     ┌────────────────┐
├─────────────────┤     │ status           │     │ EVOLUTION_LOG  │
│ id (PK)         │     │ verified         │     ├────────────────┤
│ actor           │     │ created_at       │     │ id (PK)        │
│ action          │     └──────────────────┘     │ type           │
│ resource_type   │                               │ hypothesis     │
│ resource_id     │                               │ experiment     │
│ details (JSON)  │                               │ baseline       │
│ ip_address      │                               │ result         │
│ timestamp       │                               │ status         │
└─────────────────┘                               │ version        │
                                                  │ rollback_to    │
                                                  │ approved_by    │
                                                  │ created_at     │
                                                  └────────────────┘
```

---

## 6. KEY DATA FLOWS

### 6.1 — Objective → Delivery

```
Human submits objective
    ↓
API Gateway receives POST /boss/objective
    ↓
Boss AI analyzes requirements
    ↓
Boss queries Memory for past experience
    ↓
Boss creates execution plan (task DAG)
    ↓
Boss identifies required skills
    ↓
Recruiter checks existing employees
    ↓
Recruiter creates new employees if needed
    ↓
Container Manager provisions Docker + VNC
    ↓
Scheduler dispatches ready tasks (parallel where possible)
    ↓
Employee agents execute inside containers
    ↓
Results collected via Message Bus
    ↓
Verification Engine independently tests results
    ↓
Failed? → Recovery (retry / reassign / escalate)
    ↓
All verified? → Boss aggregates final deliverable
    ↓
Memory stores lessons learned
    ↓
Report delivered to Human
```

### 6.2 — Employee Task Execution

```
Employee receives task assignment
    ↓
Reads objective, scope, acceptance criteria
    ↓
Reasons about approach (LLM call)
    ↓
Needs clarification? → Ask Boss
    ↓
Selects & executes tool (terminal, code editor, browser)
    ↓
Observes result
    ↓
Task complete? → Submit result + artifacts
    ↓
Stuck/failed? → Request help from Boss
    ↓
Awaits independent verification
    ↓
Verified? → Done. Failed? → Read feedback → Retry
```

### 6.3 — Meeting Flow

```
Boss decides meeting is needed (scheduled or event-triggered)
    ↓
Meeting Engine selects participants by role/assignment
    ↓
Agenda is set based on project state, blockers, recent events
    ↓
Each participant prepares report from actual task data
    ↓
Boss moderates: progress → problems → decisions → action items
    ↓
Meeting minutes auto-generated with structured records
    ↓
Action items assigned to employees with deadlines
    ↓
Boss tracks action items until completion in future meetings
    ↓
Executive briefing prepared for human owner
```

### 6.4 — Self-Evolution Cycle

```
Evolution Engine observes company performance metrics
    ↓
Identifies: recurring failures, slow tasks, missing skills
    ↓
Generates improvement hypotheses
    ↓
Designs experiments (new prompt, new tool, new workflow)
    ↓
Builds improved version in isolated sandbox
    ↓
Tests & benchmarks against current baseline
    ↓
Improvement verified? → Staged release (human approval if protected)
    ↓
Monitors real performance post-release
    ↓
Retains improvement OR rollbacks
```

---

## 7. DOCKER INFRASTRUCTURE

### Employee Base Image Stack
```
┌──────────────────────────────────────────┐
│ Layer 5: Role-Specific Tools             │
│ (Playwright, Terraform, ML libs, etc.)   │
├──────────────────────────────────────────┤
│ Layer 4: Agent Runtime                   │
│ (Python agent loop, tool executor, comms)│
├──────────────────────────────────────────┤
│ Layer 3: Dev Environment                 │
│ (VS Code Server, Git, Python, Node.js)   │
├──────────────────────────────────────────┤
│ Layer 2: Desktop + VNC                   │
│ (Xfce4, TigerVNC, noVNC, Chromium)       │
├──────────────────────────────────────────┤
│ Layer 1: Ubuntu 22.04 Base               │
│ (system packages, non-root user, superd) │
└──────────────────────────────────────────┘
```

### Security Controls

| Control | Implementation |
|---------|---------------|
| No privileged mode | `--privileged=false` always |
| No docker.sock | Never mount `/var/run/docker.sock` |
| Non-root user | Runs as `employee` uid 1000 |
| CPU limit | `--cpus=2` per container |
| Memory limit | `--memory=4g` per container |
| Disk limit | Quota-limited volumes |
| Process limit | `--pids-limit=256` |
| Network | Isolated bridge, DNS + proxy only |
| Seccomp | Custom restrictive profile |
| Execution timeout | Container killed after max time |

---

## 8. DEVELOPMENT PHASES

### Phase 1 — Foundation (Weeks 1–3)
- Project scaffold, configs, Docker base image
- FastAPI backend with health/auth routes
- Boss AI brain (objective → basic plan → assign)
- 2 hardcoded employees (SWE, QA) with Docker+VNC
- Basic task CRUD and assignment
- Simple result collection and status tracking
- SQLite database with core tables
- Minimal Next.js dashboard (status view)

### Phase 2 — Company Intelligence (Weeks 4–6)
- Dynamic employee creation (factory + roles)
- Skill registry with proficiency levels
- Task dependency DAG and scheduler
- Parallel task execution
- Organizational memory (vector + graph)
- LLM gateway with multi-provider support
- Inter-agent message bus
- Dashboard: employee list, task board, container status

### Phase 3 — Meetings & Org Management (Weeks 7–9)
- Meeting scheduler (daily standups, project reviews)
- Meeting conductor with structured discussions
- Meeting minutes auto-generation
- Action item tracking
- Executive briefings for human owner
- Employee progress reports grounded in evidence
- Dashboard: Boardroom / Meeting Room page

### Phase 4 — Engineering Org (Weeks 10–13)
- Multi-stage project execution
- Cross-employee code integration
- Independent QA verification with skill verification
- Security review employee
- Artifact management and versioning
- Failure recovery (retry, reassign, escalate)
- Real-time monitoring dashboard
- Embedded noVNC viewer in dashboard
- Human approval workflow

### Phase 5 — Self-Evolution (Weeks 14–17)
- Performance analytics engine
- Improvement proposal system
- Sandboxed experiment runner
- A/B benchmarking framework
- Model evaluation and optimization lab
- Employee workforce evolution (train, promote, retire)
- Version management with rollback
- Human approval for protected changes

### Phase 6 — Production Readiness (Weeks 18–20)
- Security hardening and penetration testing
- Load and chaos testing
- Cost controls and budget enforcement
- Complete audit logging
- Emergency stop mechanism
- Deployment documentation
- Operational runbooks

---

## 9. DESIGN PRINCIPLES

1. **Modularity** — Every component independently replaceable
2. **Security by Default** — Least privilege everywhere; employees can never escape sandbox
3. **Human Authority** — Emergency stop, approval gates, full audit visibility
4. **No Fake Progress** — Nothing is "done" until independently verified with evidence
5. **Incremental Delivery** — Each phase produces working, testable system
6. **Observable** — Every action logged, every cost tracked, every decision explainable
7. **Graceful Degradation** — LLM outage → queue & retry; crash → restart & recover
8. **Continuous Evolution** — System autonomously improves within safety boundaries
9. **Evidence-Based Skills** — Employee skills verified through practical tasks, not just declared
10. **Meetings With Substance** — Every meeting produces measurable outcomes, not empty conversation

---

## 10. RISK ASSESSMENT

| Risk | Impact | Mitigation |
|------|--------|-----------|
| LLM provider outage | Employees stall | Multi-provider fallback chain |
| Container resource exhaustion | System crash | Hard limits + monitoring |
| Infinite task loops | Waste resources | Timeout + max retry limits |
| Prompt injection from artifacts | Security breach | Input sanitization + sandboxing |
| Self-evolution breaks system | Production failure | Isolated experiments + rollback |
| Cost overrun | Budget exceeded | Per-task + per-employee budgets |
| Employee produces wrong output | Bad deliverable | Independent QA verification |
| Meeting system creates noise | Wasted computation | Smart scheduling, async-first |
| Docker socket exposure | Full host compromise | Never mount, strict seccomp |
| Loss of human control | Uncontrolled AI | Emergency stop, approval gates |

