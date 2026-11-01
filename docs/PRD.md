# PRODUCT REQUIREMENTS DOCUMENT (PRD)
## Autonomous Neural Company Operating System (ANCOS)

| Document Details | Value |
|------------------|-------|
| **Product Name** | ANCOS (Autonomous Neural Company Operating System) |
| **Document Version** | 1.0.0 |
| **Status** | Approved for Implementation |
| **Lead Architect** | World-Class Multi-Agent & Distributed Systems Engineer |
| **Target Release** | Phase-by-Phase Autonomous Production |

---

## 1. PRODUCT OVERVIEW & EXECUTIVE SUMMARY

ANCOS is a self-managing, self-expanding, multi-agent AI engineering organization. It simulates the operational excellence, technical hierarchy, collaboration dynamics, and continuous learning of a top-tier software engineering company.

Unlike conversational agent frameworks, ANCOS:
1. Operates a central CEO (Boss AI) capable of long-horizon planning and dynamic workforce recruitment.
2. Allocates isolated Linux desktop containers (Xfce + TigerVNC + noVNC) to each active employee.
3. Convenes automated digital boardroom meetings, daily standups, and generates auditable minutes with tracked action items.
4. Empirically verifies employee skills through practical testing before declaring them certified.
5. Employs independent QA and Security verification engines to eliminate "success-by-decree".
6. Self-evolves through sandboxed experimentation, performance benchmarking, and automatic rollback capabilities while preserving immutable human authority.

---

## 2. USER PERSONAS & STAKEHOLDERS

### Persona 1: The Human Owner / Executive Board
- **Role**: High-level director and investor.
- **Goals**: Provide strategic vision, monitor company progress, inspect deliverables, review financial costs, approve high-risk actions, and intervene with the emergency stop if necessary.
- **Interface**: Control Room Dashboard (Web) + noVNC visual workstation inspector + Executive Briefings.

### Persona 2: The Boss AI (CEO / Chief Architect)
- **Role**: Autonomous technical and operational leader.
- **Goals**: Translate human objectives into project DAGs, hire/assign employees, moderate meetings, resolve technical conflicts, enforce deadlines, ensure deliverables satisfy acceptance criteria, and plan organizational growth.

### Persona 3: Specialized AI Employees
- **Role**: Domain specialists (Backend, Frontend, DevOps, QA, Security, Research, Technical Writing).
- **Goals**: Execute assignable tasks in isolated desktop environments using code editors, terminals, browsers, and test harnesses. Participate in team meetings, explain decisions, report blockers, and evolve their skills over time.

---

## 3. FUNCTIONAL REQUIREMENTS (FR)

### FR-1: Boss AI Orchestration & Hierarchical Planning
- **FR-1.1**: The Boss AI shall accept natural language objectives and perform requirement extraction, risk analysis, and complexity scoring.
- **FR-1.2**: The Boss AI shall decompose projects into hierarchical Directed Acyclic Graphs (DAGs): Projects → Phases → Tasks → Subtasks.
- **FR-1.3**: The Boss AI shall compute critical path dependencies and identify tasks eligible for concurrent execution.
- **FR-1.4**: The Boss AI shall monitor task execution in real-time, detecting stalled tasks, infinite loops, and timeouts.

### FR-2: Dynamic AI Employee Lifecycle & Role Factory
- **FR-2.1**: The Boss AI shall dynamically recruit new employee roles when required skills are missing from the existing roster.
- **FR-2.2**: The `EmployeeFactory` shall generate unique identities (`ANC-EMP-XXXX`), job descriptions, specialized system prompts, communication styles, and approved toolsets.
- **FR-2.3**: Employees shall be reusable across multiple projects and persist in the organizational database.
- **FR-2.4**: The system shall enforce concurrency limits (`MAX_CONCURRENT_EMPLOYEES`) and terminate idle temporary workers.

### FR-3: Empirical Skill Verification & Performance Profiling
- **FR-3.1**: Each employee shall maintain an explicit profile with both *declared* and *verified* technical skills.
- **FR-3.2**: Skills shall only transition to `verified=True` upon passing practical coding or execution benchmarks evaluated independently by QA.
- **FR-3.3**: Employee performance metrics (completion time, quality score, cost in tokens/USD, failure rate) shall be continuously tracked and stored in `performance_records`.

### FR-4: Sandboxed Workstation Infrastructure (Docker + VNC)
- **FR-4.1**: Every active employee shall be assigned an isolated Docker container with an Xfce4 desktop, TigerVNC, and noVNC WebSocket proxy.
- **FR-4.2**: The container shall include terminal tools, a code editor (e.g. VS Code Server), Git, browser (Chromium/Playwright), and language runtimes.
- **FR-4.3**: Containers must run without privileged mode, without mounting `docker.sock`, and under strict CPU/memory/process limits.
- **FR-4.4**: The human owner and Boss AI shall be able to view live interactive employee desktops via authenticated noVNC links in the dashboard.

### FR-5: Autonomous Meetings & Digital Boardroom
- **FR-5.1**: The Boss AI shall autonomously schedule and moderate meetings: Daily Standups, Project Reviews, Architecture Syncs, Incident Post-Mortems, and Executive Briefings.
- **FR-5.2**: Employees shall participate using model-backed intelligence, reporting factual progress, verified receipts, and blocking issues.
- **FR-5.3**: The system shall generate structured meeting minutes recording discussions, decisions, and action items with assignees and deadlines.
- **FR-5.4**: Unfinished action items shall automatically carry over to subsequent meeting agendas until resolved.

### FR-6: Independent Quality Assurance & Verification
- **FR-6.1**: No task shall be marked complete based on the authoring employee's assertion.
- **FR-6.2**: An independent QA verifier shall execute automated test suites (e.g., `pytest`, unit/integration checks) and linting in an isolated test environment.
- **FR-6.3**: Verification reports with exact pass/fail receipts and diagnostic logs shall be appended to task records.

### FR-7: Recursive Self-Evolution & Continuous Learning
- **FR-7.1**: The system shall continuously analyze execution logs to identify recurring bottlenecks, common errors, and missing tools.
- **FR-7.2**: The `EvolutionEngine` shall formulate improvement hypotheses, create sandboxed trial branches, and benchmark proposed changes.
- **FR-7.3**: Verified improvements (prompts, tools, workflow DAGs) shall be deployed via versioned releases with automatic rollback support.
- **FR-7.4**: Core security policies, human approval gates, and authentication systems must remain immutable to autonomous modification.

### FR-8: Organizational Memory & Knowledge Graph
- **FR-8.1**: The system shall maintain an integrated memory architecture supporting Episodic, Semantic, Procedural, and Corporate Policy stores.
- **FR-8.2**: Vector similarity search (via ChromaDB or pgvector) shall enable semantic retrieval of past architectural decisions and bug fixes.
- **FR-8.3**: Lessons learned from post-mortems shall be injected into future task planning phases.

### FR-9: Security, Human Approval & Emergency Stop
- **FR-9.1**: The human owner shall be prompted for approval before any irreversible action (destructive file deletion, production infrastructure modification, financial spend).
- **FR-9.2**: An immediate emergency stop kill switch shall be accessible via both API and Dashboard to freeze all container execution and pause agent loops.
- **FR-9.3**: Comprehensive, tamper-resistant audit logs shall record all actions, API requests, tool executions, and security decisions.

### FR-10: Observability Control Room (Web Dashboard)
- **FR-10.1**: A Next.js dashboard shall display Boss AI status, active employees, live container health, and interactive Gantt/Kanban task boards.
- **FR-10.2**: The dashboard shall embed live noVNC desktop streams for inspecting employee workstations in real time.
- **FR-10.3**: Real-time log streaming and WebSocket-driven progress notifications shall be provided.

---

## 4. NON-FUNCTIONAL REQUIREMENTS (NFR)

1. **Isolation & Safety**: Container breakouts or privilege escalation must be prevented via AppArmor/seccomp profiles and non-root users.
2. **Reliability**: If an employee container crashes, the container manager must detect the failure, restart the container, and resume the task from the last valid checkpoint.
3. **Auditability**: Every model token consumed, dollar spent, tool executed, and meeting minute must be permanently persisted and queryable.
4. **Extensibility**: AI model providers (OpenAI, Anthropic, Google, local Ollama) must be hot-swappable through the unified LLM Gateway.
