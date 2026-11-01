# CONTEXT: AUTONOMOUS NEURAL COMPANY (ANCOS)
## Organizational Context, Operational Dynamics, and Domain Blueprint

---

## 1. BACKGROUND & PROBLEM SPACE

Existing multi-agent frameworks often suffer from critical structural deficiencies:
1. **Chatbot Illusion**: Agents communicate in uncontrolled text loops with no grounded execution environment, leading to hallucinations of progress without actual software artifacts.
2. **Static Configurations**: Fixed agent definitions (e.g., hardcoded "Coder" and "Reviewer") that cannot dynamically adapt to novel project requirements or recruit specialized domain experts.
3. **Lack of Human Organizational Dynamics**: Real companies thrive on meetings, status synchronization, conflict resolution, accountability, and tracked action items. Current agent systems lack these executive mechanisms.
4. **Zero Empirical Skill Verification**: Agents claim capabilities in system prompts without proof. When tasks fail, they repeatedly fail without learning or updating their competence profiles.
5. **Amnesia & Lack of True Evolution**: Context windows are confused with long-term memory. Once a session ends, all learned heuristics, architectural decisions, and failure post-mortems disappear.

**ANCOS directly solves these deficiencies** by structuring AI agents as a full corporate hierarchy with an autonomous CEO, dynamic employees with individual Linux desktop workstations, an interactive digital boardroom, empirical skill verification, and a recursive self-evolution engine.

---

## 2. OPERATIONAL DOMAIN BLUEPRINT

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             HUMAN OWNER (OWNER/BOARD)                       │
│                   ▲                                       │                 │
│      Emergency Stop / Approvals              High-Level Objectives          │
│                   │                                       ▼                 │
│  ┌────────────────┴──────────────────────────────────────────────────────┐  │
│  │                     BOSS AI (CEO & CHIEF ARCHITECT)                   │  │
│  │  - Strategic Planning        - Task Decomposition    - Meeting Chair  │  │
│  │  - Workforce Recruitment     - Resource Allocation   - Verification   │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │                                      │
│                 ┌────────────────────┴────────────────────┐                 │
│                 ▼                                         ▼                 │
│  ┌──────────────────────────────┐       ┌────────────────────────────────┐  │
│  │     AUTONOMOUS BOARDROOM     │       │     TASK & DEPENDENCY DAG      │  │
│  │  - Daily Standups            │       │  - Phase 1: Arch & DB Schema   │  │
│  │  - Architecture Sync         │       │  - Phase 2: Parallel Services  │  │
│  │  - Incident Post-Mortems     │       │  - Phase 3: QA & Security Pass │  │
│  └──────────────┬───────────────┘       └──────────────┬─────────────────┘  │
│                 │                                      │                    │
│                 └───────────────────┬──────────────────┘                    │
│                                     ▼                                       │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    EMPLOYEE WORKFORCE RUNTIME                         │  │
│  │                                                                       │  │
│  │  ┌────────────────────────┐             ┌──────────────────────────┐  │  │
│  │  │ Employee ANC-EMP-0042   │             │ Employee ANC-EMP-0043    │  │  │
│  │  │ Role: Senior Backend   │             │ Role: Senior QA          │  │  │
│  │  │ Status: Working (T-014)│             │ Status: Verifying (T-012)│  │  │
│  │  │ 🖥️ Docker + Xfce + VNC │             │ 🖥️ Docker + Xfce + VNC   │  │  │
│  │  │ 🔧 Terminal, IDE, Git  │             │ 🔧 Pytest, Browser, Logs │  │  │
│  │  └────────────────────────┘             └──────────────────────────┘  │  │
│  └──────────────────────────────────┬────────────────────────────────────┘  │
│                                     ▼                                       │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      INDEPENDENT VERIFICATION GATE                    │  │
│  │  - Adversarial Test Run  - Code Quality Linter  - Acceptance Criteria │  │
│  └──────────────────────────────────┬────────────────────────────────────┘  │
│                                     ▼                                       │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      RECURSIVE EVOLUTION ENGINE                       │  │
│  │  - Observe Failures  - Hypothesize  - Sandboxed Benchmark  - Release  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. CORE SUBSYSTEM INTERACTIONS

### 3.1 Boss AI and Employee Dynamic Lifecycle
1. **Objective Parsing**: When the Human Owner submits an objective via the Dashboard or API, the Boss AI parses requirements, estimates complexity, and queries Organizational Memory for historical precedents.
2. **Skill Gap Analysis**: The Boss identifies required competencies. If existing employees possess *verified* skills matching the requirements, they are assigned. Otherwise, the Boss dynamically invokes the `EmployeeFactory`.
3. **Containerized Provisioning**: The `ContainerManager` spins up a dedicated container using an isolated template with Xfce4 desktop, TigerVNC, noVNC proxy, and role-appropriate developer tools.
4. **Execution & Supervision**: The employee connects to the internal message bus, receives task contracts, and executes actions using its visible desktop, terminal, editor, and browser tools.
5. **Skill Verification Lifecycle**: When an employee successfully finishes a new type of task and the output is confirmed by independent QA, the skill changes from *unverified/declared* to *verified* with cryptographic or database evidence references.

### 3.2 Digital Boardroom & Autonomous Meetings
Meetings in ANCOS are structured executive sessions, not random chats:
- **Daily Standups**: Every morning or phase transition, the Boss convenes active employees. Each employee reports: *What was completed? What is incomplete? What is blocking progress? What is the verified receipt?*
- **Action Items**: Minutes are auto-generated and stored in the database. Every action item has an explicit owner and deadline, which is checked during subsequent meetings.
- **Executive Briefings**: Summaries tailored for the Human Owner detailing budget, milestone completion, open risks, and pending approval requests.

### 3.3 Organizational Memory Hierarchy
ANCOS maintains a 4-tier memory architecture:
1. **Episodic Memory**: Detailed records of past tasks, successes, failures, execution times, and container logs.
2. **Semantic Memory**: Vector-indexed knowledge base of technical concepts, libraries, API patterns, and documentation.
3. **Procedural Memory**: Verified, reusable operational recipes (e.g., how to configure OAuth2 with FastAPI, how to optimize PostgreSQL indexes).
4. **Organizational & Policy Memory**: Immutable company mission, security boundaries, and corporate governance rules.

---

## 4. SECURITY & IMMUTABLE GOVERNANCE

ANCOS enforces a **Zero Trust Sandbox Architecture**:
- **Container Isolation**: Employees run as non-root users (`uid 1000`) inside Docker containers on an isolated bridge network.
- **No Docker Socket**: The host Docker socket is never accessible to employees.
- **Resource Limits**: Hard quotas on CPU, RAM, disk I/O, process count, and execution time prevent runaway loops and denial-of-service.
- **Human Approval Gate**: Destructive filesystem operations, cloud deployments, financial transactions, and modifications to security policies require explicit human approval via the Control Room dashboard.
- **Emergency Stop**: The human owner can trigger an instantaneous kill switch that halts all worker containers, revokes API keys, and pauses the Boss AI loop.

