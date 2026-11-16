# ANCOS — Complete Architecture (Mermaid Diagrams)

Grounded in the actual source tree (`services/core/neural/`, `docker-compose.yml`,
`apps/dashboard/`). Diagrams reflect what the code and Compose file do today, not
aspirational design. Where a capability is protocol-only or not yet wired, it is
marked.

---

## 1. System Context

```mermaid
flowchart TB
    subgraph Human["Human Owner Layer"]
        Owner["Human Owner / Board"]
        Boardroom["Boardroom &amp; Meeting UI"]
        VNC["noVNC Desktop Viewer"]
    end

    Ingress["nginx Ingress<br/>:12001"]
    Dashboard["Next.js Control Room<br/>apps/dashboard"]
    API["Core API — FastAPI<br/>:12000<br/>REST + WebSocket"]

    subgraph Intel["Intelligence Layer"]
        Boss["Boss AI / CEO Brain<br/>boss/brain.py"]
        Planner["Task Planner (DAG)<br/>boss/planner.py"]
        Delegator["Task Delegator<br/>boss/delegator.py"]
        Gov["Boardroom Governance<br/>governance/engine.py"]
        Evol["Self-Evolution Engine<br/>evolution/lifecycle.py"]
    end

    subgraph Infra["Infrastructure Layer"]
        LLM["LLM Gateway<br/>llm/gateway.py"]
        Laya["Laya System-1 Inference<br/>decision_intelligence/system_one"]
        Mem["Organizational Memory<br/>memory/store.py"]
        Bus["Redis Message Bus<br/>comms/bus.py"]
        Sec["Security / Policy<br/>security/*"]
    end

    subgraph Runtime["Employee Runtime Layer"]
        Broker["Restricted Docker Broker<br/>containers/broker_api.py :12002"]
        WS["Employee Workstations<br/>ancos-desktop-anc-emp-*"]
        GW["Workstation Gateway<br/>:12003 → 6080"]
    end

    Owner --> Ingress
    Boardroom --> Ingress
    VNC --> Ingress
    Ingress --> Dashboard
    Ingress --> GW
    Dashboard --> API
    API --> Boss
    Boss --> Planner
    Boss --> Delegator
    Boss --> Gov
    Boss --> Evol
    Boss --> LLM
    Boss --> Mem
    Boss --> Bus
    Planner --> Laya
    LLM --> Laya
    Delegator --> Sec
    Boss --> Broker
    Broker --> WS
    GW --> WS
    API --> Bus
```

---

## 2. Deployment Topology (docker-compose)

```mermaid
flowchart LR
    subgraph Host["Host"]
        direction TB
        Redis["redis<br/>127.0.0.1:6379"]
        DataInit["data-init<br/>(one-shot volume prep)"]
    end

    subgraph NetA["network: ancos-network (bridge)"]
        CoreAPI["core-api<br/>:12000"]
        TerminalWorker["terminal-worker<br/>neural.workers.terminal"]
        GuiWorker["gui-worker<br/>neural.workers.gui"]
        TerminalVerifier["terminal-verifier<br/>neural.workers.verifier"]
        DashboardSvc["dashboard<br/>Next.js"]
        IngressSvc["ingress<br/>nginx :12001"]
        WSGateway["workstation-gateway<br/>:12003 → 6080"]
    end

    subgraph NetB["network: ancos-docker-control (internal)"]
        BrokerSvc["docker-broker<br/>RestrictedDockerBroker :12002"]
    end

    subgraph NetC["network: ancos-workstation-egress (bridge)"]
        EmployeeDesktops["Employee desktops<br/>ancos-desktop-anc-emp-*<br/>Xvfb + xdotool + VNC"]
    end

    DataInit --> Redis
    Redis --- CoreAPI
    Redis --- TerminalWorker
    Redis --- GuiWorker
    Redis --- TerminalVerifier
    IngressSvc --> DashboardSvc
    IngressSvc --> WSGateway
    CoreAPI --> BrokerSvc
    GuiWorker --> CoreAPI
    TerminalWorker --> CoreAPI
    TerminalVerifier --> CoreAPI
    BrokerSvc --> EmployeeDesktops
    WSGateway --> EmployeeDesktops
    CoreAPI --- DashboardSvc
```

Notes:
- `docker-broker` sits on the internal-only `ancos-docker-control` network; the
  application never mounts `/var/run/docker.sock` directly.
- `workstation-gateway` and `ingress` bridge `ancos-network` with
  `ancos-workstation-egress` so browsers can reach noVNC.
- Ports bind to `ANCOS_BIND_HOST` (default `127.0.0.1`).

---

## 3. Boss Brain — Objective to Verified Delivery

```mermaid
flowchart TB
    Start(["Human objective"]) --> Analyze["analyze_and_plan()<br/>boss/brain.py"]
    Analyze --> Decompose["Hierarchical decomposition<br/>Project → Phase → Task → Subtask"]
    Decompose --> DAG["Build dependency DAG<br/>tasks/dependencies.py"]
    DAG --> Recruit["recruit_workforce_for_plan()<br/>employees/factory.py"]
    Recruit --> SkillMatch["Skill matching &amp;<br/>verified-skill gaps"]
    SkillMatch --> Assign["Assign via delegator<br/>boss/delegator.py"]
    Assign --> DAGExec["execute_project_dag()<br/>tasks/scheduler.py"]
    DAGExec --> Dispatch["Dispatch ready tasks<br/>(topological + concurrency caps)"]

    Dispatch --> TerminalTask{"Execution mode?"}
    TerminalTask -->|terminal| TWorker["terminal-worker"]
    TerminalTask -->|gui / hybrid| GWorker["gui-worker"]
    TerminalTask -->|hybrid| Hybrid["HybridExecutionCoordinator<br/>workers/hybrid.py"]

    TWorker --> Submit["Submit receipt<br/>(task stays RUNNING)"]
    GWorker --> Submit
    Hybrid --> Submit
    Submit --> Verifier["Independent verifier<br/>workers/verifier.py"]
    Verifier --> Verdict{"Verdict"}
    Verdict -->|pass| Complete["Atomic: receipt + TaskResult<br/>+ COMPLETED + fencing bump"]
    Verdict -->|reject| Retry["Attempt remains open<br/>for retry"]
    Retry --> Dispatch
    Complete --> Report["build_project_report()<br/>boss/brain.py"]
    Report --> Human(["Human owner / dashboard"])
```

---

## 4. Task Worker Claim Protocol

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker<br/>(terminal / gui)
    participant API as Core API<br/>/internal/worker
    participant DB as Database
    participant B as Docker Broker
    participant V as Verifier

    W->>API: POST /tasks/claim {worker_id}
    API->>DB: Verify employee reserved + workstation running
    API->>B: Refresh workstation status
    API->>DB: Lease task, issue fencing_token
    API-->>W: task + acceptance_criteria + container_id

    W->>API: POST /tasks/{id}/start {fencing_token}
    API->>DB: Recheck live workstation → RUNNING
    loop every heartbeat interval
        W->>API: POST /tasks/{id}/heartbeat {fencing_token}
        API->>DB: Extend lease
    end

    W->>API: POST /tasks/{id}/submit {receipt}
    API->>DB: Store receipt, status = submitted_for_verification
    Note over API,DB: Task deliberately stays RUNNING

    V->>API: GET /internal/verifier/queue
    API-->>V: submitted attempts + acceptance criteria
    V->>API: POST /tasks/{id}/attempts/{aid}/review {verdict}
    alt verdict = pass
        API->>DB: Atomic: receipt + TaskResult + COMPLETED + fencing bump
    else verdict = reject
        API->>DB: Attempt stays open for retry
    end

    Note over W,V: Stale workers cannot renew or submit after fencing-token revocation.
```

---

## 5. GUI Execution Loop (Visual-First, No Cheating)

```mermaid
sequenceDiagram
    autonumber
    participant GW as gui-worker
    participant API as Core API
    participant Eng as GuiWorkerEngine
    participant Drv as ContainerDesktopDriver
    participant Desk as Employee Desktop<br/>(Xvfb + xdotool)

    GW->>API: POST /gui/plan {fencing_token}
    API-->>GW: concrete step plan + expected_text
    GW->>API: POST /gui/execute
    API->>Eng: execute(steps, expected_text)

    Eng->>Drv: assert_available()
    Drv->>Desk: probe scrot + xdotool + X display
    Eng->>Drv: reset_editor_state()
    Drv->>Desk: clear stale editor autosave

    Eng->>Drv: capture_frame() [before]
    Drv->>Desk: scrot → PNG → ScreenFrame
    loop each plan step
        Eng->>Drv: capture fresh frame
        Drv->>Desk: scrot
        Eng->>Eng: ground target via OCR / visual grounding
        Note over Eng: refuses to act blind if target not found
        Eng->>Drv: dispatch motor (mouse / key)
        Drv->>Desk: xdotool mousemove / click / type / key
    end
    Eng->>Drv: capture_frame() [after]
    Drv->>Desk: scrot

    Eng->>Eng: IndependentVisualVerifier.verify_postcondition()
    Note over Eng: expected_text must be visible in real pixels
    Eng-->>GW: GuiWorkerResult (success only if verified)
    GW->>API: POST /submit {receipt + screenshot hashes}
```

---

## 6. GUI Intelligence — Internal Architecture

```mermaid
flowchart TB
    subgraph Worker["worker.py"]
        Engine["GuiWorkerEngine<br/>require_real_desktop=True"]
    end

    subgraph Runtime["runtime/"]
        Driver["ContainerDesktopDriver<br/>scrot + xdotool via broker"]
        Motor["build_real_motor_controllers"]
        Planner["planner.derive_gui_steps"]
        Authoring["authoring.py<br/>model-driven content"]
    end

    subgraph Perception["perception/"]
        Capture["screen_capture"]
        OCR["ocr_runtime / pixel_ocr"]
        Ground["visual_grounding"]
        Det["object_detection"]
        Change["screen_change"]
        Enc["visual_encoder"]
    end

    subgraph Brain["brain/"]
        Cog["DualBrainCognitiveController"]
        Fast["fast_policy"]
        TPlan["task_planner"]
    end

    subgraph Action["action/"]
        Loop["GUIExecutionLoop"]
        Mouse["mouse_controller"]
        Key["keyboard_controller"]
        Batch["action_batcher"]
    end

    subgraph World["world_model/"]
        Belief["visual_belief"]
        Trans["transition_tracker"]
        Win["window_state"]
        Screen["screen_state"]
    end

    subgraph Safety["safety/"]
        Risk["risk_guard"]
        Policy["gui_action_policy"]
    end

    subgraph Verify["verification/"]
        VVerifier["visual_verifier"]
        Complete["task_completion"]
    end

    subgraph Recovery["recovery/"]
        Fail["failure_detector"]
        Vis["visibility_recovery"]
        State["state_recovery"]
    end

    Engine --> Driver
    Engine --> Motor
    Engine --> Planner
    Engine --> Authoring
    Driver --> Capture
    Capture --> OCR
    Capture --> Det
    Capture --> Change
    Capture --> Enc
    OCR --> Ground
    Det --> Ground
    Ground --> Loop
    Motor --> Loop
    Loop --> Mouse
    Loop --> Key
    Loop --> Batch
    Loop --> Risk
    Risk --> Policy
    Loop --> Belief
    Belief --> Trans
    Trans --> Win
    Win --> Screen
    Cog --> Fast
    Cog --> TPlan
    Cog --> Loop
    Loop --> VVerifier
    VVerifier --> Complete
    Loop --> Fail
    Fail --> Vis
    Fail --> State
```

---

## 7. Laya Decision Intelligence (System 1)

```mermaid
flowchart TB
    Req(["Decision request"]) --> Router["DecisionRouter<br/>routing/decision_router.py"]
    Router --> Complexity["ComplexityClassifier"]
    Complexity --> Candidate["CandidateSelector"]

    Candidate --> Registry["ModelRegistry<br/>system_one/model_registry.py"]
    Registry --> Adapter["LayaAdapter<br/>laya_adapter.py"]
    Adapter --> Detect{"Runtime detect<br/>+ SHA-256 verify"}

    Detect -->|weights OK| Infer["Real inference<br/>ModernBERT checkpoints"]
    Detect -->|missing / offline / hash fail| Fallback["ModelRuntime.FALLBACK<br/>deterministic heuristics"]

    Infer --> Choice["predict_choice"]
    Infer --> Score["predict_score"]
    Infer --> Noul["predict_noul"]

    Fallback --> Choice
    Fallback --> Score
    Fallback --> Noul

    Choice --> Escalation["Escalation / threshold checks"]
    Score --> Escalation
    Noul --> Escalation
    Escalation --> Calib["Calibration monitor<br/>+ reliability"]
    Calib --> Out(["Advisory signal"])

    Out --> Sovereign["Policy engine + Human Approval Gate<br/>retain final authority"]

    subgraph Learn["Learning / Governance"]
        Hist["decision_history"]
        FB["feedback_collector"]
        DS["dataset_builder"]
        Shadow["shadow_mode"]
        Promo["promotion / rollback"]
    end
    Out --> Hist
    Hist --> FB
    FB --> DS
    DS --> Shadow
    Shadow --> Promo
```

---

## 8. Data Model (core relationships)

```mermaid
erDiagram
    PROJECTS ||--o{ TASKS : contains
    PROJECTS ||--o{ ARTIFACTS : produces
    PROJECTS ||--o{ PROPOSALS : "scoped to"
    TASKS ||--o{ TASKS : "parent / subtask"
    TASKS ||--o{ TASK_DEPENDENCIES : "depends on"
    TASKS ||--o{ TASK_ATTEMPTS : "attempted by"
    TASKS ||--o{ TASK_RESULTS : "results in"
    TASKS }o--|| EMPLOYEES : "assigned to"
    TASKS ||--o{ ARTIFACTS : produces
    EMPLOYEES ||--o{ EMPLOYEE_SKILLS : has
    EMPLOYEES ||--o{ SKILL_CERTIFICATIONS : earns
    EMPLOYEES ||--o{ PERFORMANCE_RECORDS : accrues
    EMPLOYEES }o--o| CONTAINERS : "assigned desktop"
    MEETINGS ||--o{ ACTION_ITEMS : produces
    PROPOSALS ||--o{ VOTES : receives
    PROPOSALS ||--o{ DECISION_LEDGER : records
    METHOD_RECORDS ||--o{ METHOD_BENCHMARKS : benchmarked

    PROJECTS {
        string id PK
        string name
        string objective
        string status
    }
    TASKS {
        string id PK
        string task_id UK
        string project_id FK
        string parent_task_id FK
        string assigned_to FK
        string status
        string execution_mode
    }
    TASK_ATTEMPTS {
        string id PK
        string task_id FK
        int fencing_token
        int attempt_number
    }
    TASK_DEPENDENCIES {
        string task_id FK
        string depends_on FK
    }
    EMPLOYEES {
        string id PK
        string employee_id UK
        string title
        string status
        string container_id FK
    }
    EMPLOYEE_SKILLS {
        string id PK
        string employee_id FK
        string skill
        bool verified
    }
    SKILL_CERTIFICATIONS {
        string id PK
        string employee_id FK
    }
    CONTAINERS {
        string id PK
        string docker_id
        string status
    }
    RESOURCE_LEASES {
        string id PK
        string resource_key
        int fencing_token
    }
    APPROVALS {
        string id PK
        string operation
        string status
    }
    AUDIT_LOG {
        string id PK
        string actor
        string action
    }
    DECISION_RECORDS {
        string id PK
    }
```

---

## 9. Self-Evolution Lifecycle

```mermaid
stateDiagram-v2
    [*] --> OBSERVE
    OBSERVE --> IDENTIFY : collect failures, gaps, requirements
    IDENTIFY --> HYPOTHESIZE : gap_analysis.py
    HYPOTHESIZE --> EXPERIMENT : proposal_engine.py
    EXPERIMENT --> BUILD : run_sandboxed_experiment()
    BUILD --> TEST : candidate_staging.py
    TEST --> BENCHMARK : multi_gate_verifier.py
    BENCHMARK --> COMPARE : progression.py
    COMPARE --> SELECT : measured improvement
    SELECT --> DEPLOY : human approval for protected changes
    DEPLOY --> MONITOR
    MONITOR --> RETAIN : improvement holds
    MONITOR --> ROLLBACK : regression detected
    ROLLBACK --> OBSERVE
    RETAIN --> OBSERVE
    SELECT --> [*] : no verified gain
```

---

## 10. Governance & Boardroom

```mermaid
flowchart LR
    Propose["create_proposal()"] --> Deliberate["run_deliberation()"]
    Deliberate --> Vote["open_voting() → cast_vote()"]
    Vote --> Finalize["finalize_voting()"]
    Finalize --> Outcome{"Outcome"}
    Outcome -->|approved| Exec["execute_proposal()"]
    Outcome -->|vetoed| Veto["veto_proposal()<br/>(human sovereignty)"]
    Outcome -->|cancelled| Cancel["cancel_proposal()"]
    Exec --> Ledger["decision_ledger"]
    Veto --> Ledger
    Cancel --> Ledger
    Ledger --> Audit["audit_log (hash-chained)"]
```

---

## 11. Security & Human Sovereignty

```mermaid
flowchart TB
    Action(["Privileged / destructive action"]) --> Kill{"Emergency kill switch active?"}
    Kill -->|yes| Halt["Guard blocks action"]
    Kill -->|no| Policy{"Policy check<br/>security/policy.py"}
    Policy -->|denied| Block["Blocked + audited"]
    Policy -->|needs approval| Gate["Approval gate<br/>security/approval_gates.py"]
    Gate --> Approver{"Distinct approver?<br/>APPROVER_API_TOKEN"}
    Approver -->|no| Block
    Approver -->|yes| Consume["Consume approval atomically<br/>(no replay)"]
    Consume --> Broker["RestrictedDockerBroker"]
    Broker --> Validate["validate container name / id /<br/>image / volume mounts"]
    Validate --> Harden["cap_drop=ALL, no-new-privileges,<br/>confined workspace mount"]
    Harden --> Execute["Execute on employee desktop"]
    Execute --> AuditLog["audit_log (tamper-evident)"]

    Policy -->|allowed| Execute

    subgraph Prohibited["Hard prohibitions"]
        P1["No docker.sock mount"]
        P2["No host root mount"]
        P3["No privileged containers"]
        P4["No direct DOM / a11y / Playwright<br/>bypass for GUI tasks"]
    end
    Validate -.-> Prohibited
```

---

## Reality Notes

- **Live vs protocol-only.** The worker/verifier endpoints are real, credential-
  authenticated protocol surfaces. A full autonomous GUI run additionally needs a
  real desktop adapter and an independently operated verifier process; without them
  the system must not claim autonomous work completed.
- **Fallback behaviour.** Laya falls back to deterministic heuristics when weights
  are absent, offline, or fail SHA-256 verification; the policy engine and human
  approval gates keep final authority over privileged actions.
- **Databases.** `docker-compose.yml` runs on SQLite for the default profile; the
  code also supports PostgreSQL via `DATABASE_URL`. Alembic owns schema versioning.
