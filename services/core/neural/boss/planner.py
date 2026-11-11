import inspect
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from neural.constants import ExecutionMode
from neural.exceptions import ModelProviderError, UnsupportedExecutionModeError
from neural.llm.gateway import LLMGateway


class PlannedTask(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=3, max_length=180)
    objective: str = Field(default="", max_length=2000)
    scope: str = Field(min_length=1, max_length=2000)
    role_required: str = Field(min_length=1, max_length=100)
    skills_required: list[str] = Field(min_length=1, max_length=12)
    execution_mode: ExecutionMode = ExecutionMode.TERMINAL
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: str = Field(min_length=8, max_length=2000)
    subtasks: list[str] = Field(default_factory=list, max_length=12)
    estimated_hours: float = Field(default=1, gt=0, le=200)
    max_tokens: int = Field(default=12000, ge=1000, le=200000)


class PlannedPhase(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=20)


class ObjectivePlan(BaseModel):
    code_name: str = Field(min_length=1, max_length=60)
    complexity: Literal["low", "medium", "high"]
    requirements: list[str] = Field(default_factory=list, max_length=30)
    clarification_required: bool = False
    clarifying_questions: list[str] = Field(default_factory=list, max_length=8)
    technical_risks: list[str] = Field(default_factory=list, max_length=20)
    mitigation_strategies: list[str] = Field(default_factory=list, max_length=20)
    phases: list[PlannedPhase] = Field(default_factory=list, max_length=12)


def validate_plan_dag(plan: ObjectivePlan) -> None:
    """
    Strict DAG cycle and dependency validation:
    1. Rejects duplicate task IDs.
    2. Rejects self-dependencies.
    3. Rejects unknown dependencies.
    4. Enforces Kahn's algorithm acyclicity check.
    5. Rejects forward dependencies.
    """
    raw_ids = [task.task_id for phase in plan.phases for task in phase.tasks]
    if len(raw_ids) != len(set(raw_ids)):
        raise ModelProviderError("Boss plan rejected: duplicate task IDs.")
    known_ids = set(raw_ids)

    for phase in plan.phases:
        for task in phase.tasks:
            if task.task_id in task.dependencies:
                raise ModelProviderError(f"Boss plan rejected: self-dependency in {task.task_id}.")
            for dependency in task.dependencies:
                if dependency not in known_ids:
                    raise ModelProviderError(
                        f"Boss plan rejected: invalid or forward dependency in {task.task_id}."
                    )

    # Kahn's algorithm for acyclicity check
    in_degree = {tid: 0 for tid in raw_ids}
    adj: dict[str, list[str]] = {tid: [] for tid in raw_ids}
    for phase in plan.phases:
        for task in phase.tasks:
            for dep in task.dependencies:
                adj[dep].append(task.task_id)
                in_degree[task.task_id] += 1

    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    visited_count = 0
    while queue:
        curr = queue.pop(0)
        visited_count += 1
        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited_count != len(raw_ids):
        raise ModelProviderError("Boss plan rejected: dependency cycle detected.")

    # Topological forward-dependency check: tasks may only depend on earlier tasks in plan order
    seen_ids: set[str] = set()
    for phase in plan.phases:
        for task in phase.tasks:
            for dependency in task.dependencies:
                if dependency not in seen_ids:
                    raise ModelProviderError(
                        f"Boss plan rejected: invalid or forward dependency in {task.task_id}."
                    )
            seen_ids.add(task.task_id)


class TaskPlanner:
    """
    Hierarchical Task Decomposition Engine.
    Breaks high-level objectives into Projects -> Phases -> Tasks -> Subtasks
    with explicit dependency DAGs, requirement extraction, and risk analysis.
    """

    def __init__(
        self,
        llm_gateway: Optional[LLMGateway] = None,
        supported_modes: Optional[set[ExecutionMode]] = None,
    ) -> None:
        self.llm_gateway = llm_gateway
        self.supported_modes = supported_modes

    @staticmethod
    def _has_configured_provider(gateway: LLMGateway) -> bool:
        if gateway.custom_invoker is not None:
            return True
        settings = gateway.settings
        return settings.BOSS_LLM_PLANNING_ENABLED and any(
            getattr(settings, name, None)
            for name in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
        )

    async def _decompose_with_model(
        self,
        objective: str,
        clarifications: Optional[dict[str, str]] = None,
        db_session: Optional[Any] = None,
        project_id: Optional[str] = None,
        budget_usd: Optional[float] = None,
    ) -> dict[str, Any]:
        assert self.llm_gateway is not None
        budget_scope_caps = None
        if project_id is not None and budget_usd is not None:
            month_scope = datetime.now(timezone.utc).strftime("%Y-%m")
            budget_scope_caps = {
                f"organization-month:{month_scope}": self.llm_gateway.cost_tracker.monthly_hard_cap_usd,
                f"project:{project_id}": budget_usd,
            }

        prompt = (
            "Create a practical software project plan for this human objective. "
            "First decide whether an essential outcome, target, or constraint is missing. "
            "If missing information would materially change the plan, set clarification_required=true, "
            "ask up to three short, specific clarifying_questions, and leave phases empty. "
            "Do not invent the owner's intent. Otherwise set clarification_required=false and plan normally. "
            "Choose the number of phases and tasks to match the objective's scope and the owner's requested "
            "granularity. Respect explicit atomicity constraints: when the owner asks for one task, one "
            "deliverable, or no decomposition, return exactly one phase containing exactly one task; do not "
            "split implementation, test creation, and test execution into separate tasks. For broader "
            "objectives without an explicit atomicity constraint, use 2-6 phases and concrete, independently "
            "verifiable tasks an engineering team can deliver. "
            "Every task needs measurable acceptance criteria and required skills. "
            "Every task must declare its execution_mode as one of gui, terminal, hybrid, or authorized_tools. "
            "In this deployment, only terminal tasks have a long-running worker consumer. "
            "Do not assign gui, hybrid, or authorized_tools unless the objective cannot be met safely "
            "through terminal work; those modes will be rejected before any project or employee is persisted. "
            "Dependencies must reference task_id values in this plan, point only "
            "to earlier tasks, and form an acyclic graph. Do not claim work is "
            "already completed.\n\nOBJECTIVE:\n" + objective
        )
        if clarifications:
            clarification_text = "\n".join(
                f"- Q: {k} | A: {v}" for k, v in clarifications.items() if v
            )
            if clarification_text:
                prompt += (
                    f"\n\nCLARIFICATIONS PROVIDED BY OWNER:\n{clarification_text}\n"
                    "The owner has answered clarifying questions. Incorporate these clarifications, "
                    "set clarification_required=false, and provide the complete execution plan."
                )

        plan, response = await self.llm_gateway.generate_structured(
            prompt=prompt,
            response_model=ObjectivePlan,
            route="boss_brain",
            system_prompt="You are ANCOS Boss. Plan only; never report unperformed work as complete.",
            temperature=0.1,
            max_tokens=3500,
            db_session=db_session,
            project_id=project_id,
            budget_scope_caps=budget_scope_caps,
        )

        if plan.clarification_required:
            questions = [question.strip() for question in plan.clarifying_questions if question.strip()]
            if not questions:
                raise ModelProviderError(
                    "Boss plan rejected: clarification was requested without a clarifying question."
                )
            return {
                "code_name": plan.code_name,
                "complexity": plan.complexity,
                "requirements": plan.requirements,
                "risk_analysis": {
                    "technical_risks": plan.technical_risks,
                    "mitigation_strategies": plan.mitigation_strategies,
                },
                "clarification_required": True,
                "clarifying_questions": questions[:3],
                "plan_source": "llm",
                "planning_model": response.model,
                "phases": [],
            }
        if not plan.phases:
            raise ModelProviderError("Boss plan rejected: no executable phases were planned.")

        active_supported = (
            self.supported_modes
            if self.supported_modes is not None
            else {ExecutionMode.TERMINAL, ExecutionMode.TERMINAL_ONLY}
        )
        unsupported_modes = sorted({
            task.execution_mode.value
            for phase in plan.phases
            for task in phase.tasks
            if task.execution_mode not in active_supported
            and ExecutionMode.normalize(task.execution_mode) not in active_supported
        })
        if unsupported_modes:
            raise UnsupportedExecutionModeError(
                "No active task worker is registered for execution mode(s): "
                + ", ".join(unsupported_modes)
                + ". No project or employee was persisted."
            )

        requirements = plan.requirements or [f"Deliver the owner's stated objective: {objective.strip()}"]

        validate_plan_dag(plan)

        phase_dicts: list[dict[str, Any]] = []
        for phase_index, phase in enumerate(plan.phases, start=1):
            tasks = []
            for task_index, task in enumerate(phase.tasks, start=1):
                tasks.append({
                    "task_id": f"TASK-{phase_index:02d}-{task_index:02d}-{uuid.uuid4().hex[:4].upper()}",
                    "source_task_id": task.task_id,
                    "title": task.title,
                    "objective": task.objective or task.title,
                    "scope": task.scope,
                    "role_required": task.role_required,
                    "skills_required": task.skills_required,
                    "execution_mode": task.execution_mode,
                    "dependencies": task.dependencies,
                    "acceptance_criteria": task.acceptance_criteria,
                    "subtasks": task.subtasks,
                    "resource_budget": {
                        "estimated_hours": task.estimated_hours,
                        "max_tokens": task.max_tokens,
                    },
                })
            phase_dicts.append({
                "phase_id": f"PHASE-{phase_index:02d}",
                "title": phase.title,
                "tasks": tasks,
            })

        id_map = {
            task["source_task_id"]: task["task_id"]
            for phase in phase_dicts for task in phase["tasks"]
        }
        for phase in phase_dicts:
            for task in phase["tasks"]:
                task["dependencies"] = [id_map[value] for value in task["dependencies"]]

        return {
            "code_name": plan.code_name,
            "complexity": plan.complexity,
            "requirements": requirements,
            "risk_analysis": {
                "technical_risks": plan.technical_risks,
                "mitigation_strategies": plan.mitigation_strategies,
            },
            "phases": phase_dicts,
            "plan_source": "llm",
            "planning_model": response.model,
        }

    def score_complexity(self, objective: str) -> str:
        """Evaluates complexity of an objective based on scope indicators."""
        if not objective or not objective.strip():
            return "low"
        obj_lower = objective.lower()
        high_indicators = [
            "distributed", "microservices", "e-commerce", "enterprise",
            "production", "real-time", "pipeline", "streaming", "security audit",
            "resilient", "cloud-native", "scale", "fault-tolerant"
        ]
        medium_indicators = [
            "api", "backend", "dashboard", "service", "database", "crud", "auth"
        ]
        score = sum(1 for ind in high_indicators if ind in obj_lower) * 2
        score += sum(1 for ind in medium_indicators if ind in obj_lower)
        if len(objective.split()) > 15:
            score += 2

        if score >= 4:
            return "high"
        elif score >= 2:
            return "medium"
        return "low"

    def extract_requirements(self, objective: str) -> list[str]:
        """Extracts key engineering requirements from natural language objective."""
        if not objective or not objective.strip():
            return ["Core software component implementation", "Functional unit tests"]

        reqs: list[str] = []
        obj_lower = objective.lower()

        if any(w in obj_lower for w in ["api", "rest", "fastapi", "microservice", "service", "backend"]):
            reqs.append("Scalable RESTful API gateway with OpenAPI contracts and input validation")
        if any(w in obj_lower for w in ["database", "schema", "store", "persist", "data", "postgres", "sqlite"]):
            reqs.append("Relational database schema modeling with foreign key integrity and migrations")
        if any(w in obj_lower for w in ["auth", "jwt", "oauth", "security", "token"]):
            reqs.append("Secure authentication & authorization subsystem with token expiration")
        if any(w in obj_lower for w in ["dashboard", "ui", "frontend", "react", "client", "web"]):
            reqs.append("Responsive client dashboard with real-time state visualization")
        if any(w in obj_lower for w in ["stream", "pipeline", "message", "queue", "redis", "events"]):
            reqs.append("Asynchronous event messaging bus with dead-letter queue handling")

        # Guarantee at least 2 baseline engineering requirements
        if not reqs:
            reqs.append("Modular software architecture design")
            reqs.append("Core business domain logic implementation")
        reqs.append("Comprehensive automated test coverage and independent QA receipt verification")
        return reqs

    def assess_risks(self, objective: str) -> dict[str, list[str]]:
        """Performs proactive technical risk assessment and proposes mitigations."""
        obj_lower = objective.lower() if objective else ""
        risks: list[str] = [
            "Specification ambiguity leading to architectural misalignment",
            "Unverified dependency contracts across distributed components"
        ]
        mitigations: list[str] = [
            "Strict typed ActionContract schemas and pre/post-condition enforcement",
            "Independent verification engine running automated test harnesses"
        ]

        if "distributed" in obj_lower or "microservice" in obj_lower or "stream" in obj_lower:
            risks.append("Network latency and transient message loss across worker nodes")
            mitigations.append("Exponential backoff retry loops with circuit breakers and fallback providers")

        if "security" in obj_lower or "auth" in obj_lower or "production" in obj_lower:
            risks.append("Privilege escalation or unauthorized access to internal resources")
            mitigations.append("Non-root container execution (uid 1000) and strict seccomp/apparmor sandboxing")

        return {
            "technical_risks": risks,
            "mitigation_strategies": mitigations
        }

    async def decompose_objective(
        self,
        objective: str,
        clarifications: Optional[dict[str, str]] = None,
        db_session: Optional[Any] = None,
        project_id: Optional[str] = None,
        budget_usd: Optional[float] = None,
    ) -> dict[str, Any]:
        """
        Decomposes natural language objective into hierarchical execution plan:
        Projects -> Phases -> Tasks -> Subtasks
        """
        if objective and objective.strip() and self.llm_gateway and self._has_configured_provider(self.llm_gateway):
            kwargs: dict[str, Any] = {
                "db_session": db_session,
                "project_id": project_id,
                "budget_usd": budget_usd,
            }
            sig = inspect.signature(self._decompose_with_model)
            if "clarifications" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                kwargs["clarifications"] = clarifications
            return await self._decompose_with_model(objective.strip(), **kwargs)

        normalized = " ".join(re.findall(r"[a-z0-9]+", objective.lower())) if objective else ""
        words = normalized.split()
        vague_objectives = {
            "do it",
            "fix it",
            "fix this",
            "make it better",
            "improve it",
            "build something",
            "create something",
            "make something",
        }
        has_clarifications = bool(clarifications and any(bool(v and v.strip()) for v in clarifications.values()))
        if normalized and (normalized in vague_objectives or len(words) < 3) and not has_clarifications:
            canonical_clarification = ObjectivePlan(
                code_name="Clarification",
                complexity="low",
                requirements=[],
                clarification_required=True,
                clarifying_questions=[
                    "What specific outcome should ANCOS deliver, and what would count as success?"
                ],
                technical_risks=self.assess_risks(objective)["technical_risks"],
                mitigation_strategies=self.assess_risks(objective)["mitigation_strategies"],
                phases=[],
            )
            return {
                "code_name": canonical_clarification.code_name,
                "complexity": canonical_clarification.complexity,
                "requirements": canonical_clarification.requirements,
                "risk_analysis": {
                    "technical_risks": canonical_clarification.technical_risks,
                    "mitigation_strategies": canonical_clarification.mitigation_strategies,
                },
                "clarification_required": True,
                "clarifying_questions": list(canonical_clarification.clarifying_questions),
                "plan_source": "rules_fallback",
                "phases": [],
            }

        words = re.findall(r"\b[A-Za-z0-9]+\b", objective) if objective else []
        code_name = words[0].capitalize() if words else "Omega"
        complexity = self.score_complexity(objective)
        requirements = self.extract_requirements(objective)
        if clarifications:
            for q, a in clarifications.items():
                if a and a.strip():
                    requirements.append(f"Owner clarification: {q} -> {a.strip()}")
        risk_analysis = self.assess_risks(objective)

        canonical_fallback = ObjectivePlan(
            code_name=code_name,
            complexity=complexity,
            requirements=requirements,
            clarification_required=False,
            clarifying_questions=[],
            technical_risks=risk_analysis["technical_risks"],
            mitigation_strategies=risk_analysis["mitigation_strategies"],
            phases=[
                PlannedPhase(
                    title="System Architecture & Foundation",
                    tasks=[
                        PlannedTask(
                            task_id="task-arch-01",
                            title="Domain Modeling & Database Schema Design",
                            objective="Design relational database schema and migration models.",
                            scope="Database schema design and SQLAlchemy ORM models.",
                            role_required="senior-backend-engineer",
                            skills_required=["System Design", "PostgreSQL"],
                            execution_mode=ExecutionMode.TERMINAL,
                            dependencies=[],
                            acceptance_criteria="Fully normalized schema with migrations and foreign key integrity.",
                            subtasks=[
                                "Define SQLAlchemy ORM models with explicit foreign key constraints",
                                "Configure database migrations and connection pool settings",
                            ],
                            estimated_hours=3,
                            max_tokens=40000,
                        ),
                        PlannedTask(
                            task_id="task-api-02",
                            title="API Gateway & Security Specification",
                            objective="Specify API gateway contracts and auth flow.",
                            scope="API routes, Pydantic schemas, and auth middleware.",
                            role_required="senior-backend-engineer",
                            skills_required=["FastAPI", "REST APIs"],
                            execution_mode=ExecutionMode.TERMINAL,
                            dependencies=[],
                            acceptance_criteria="FastAPI routes defined with OpenAPI spec and JWT auth contracts.",
                            subtasks=[
                                "Create FastAPI route definitions with typed Pydantic request/response models",
                                "Implement authentication dependency and error handling middleware",
                            ],
                            estimated_hours=3,
                            max_tokens=40000,
                        ),
                    ],
                ),
                PlannedPhase(
                    title="Parallel Service Implementation & UI",
                    tasks=[
                        PlannedTask(
                            task_id="task-logic-03",
                            title="Core Business Logic Services",
                            objective="Implement business logic and services.",
                            scope="Core business domain services.",
                            role_required="senior-backend-engineer",
                            skills_required=["Python", "FastAPI"],
                            execution_mode=ExecutionMode.TERMINAL,
                            dependencies=["task-arch-01", "task-api-02"],
                            acceptance_criteria="Business logic implemented with unit test coverage > 90%.",
                            subtasks=[
                                "Implement core business service functions and repositories",
                                "Integrate transactional boundaries and logging",
                            ],
                            estimated_hours=5,
                            max_tokens=60000,
                        ),
                        PlannedTask(
                            task_id="task-ui-04",
                            title="Interactive Client Dashboard & State",
                            objective="Build interactive frontend dashboard.",
                            scope="Client dashboard frontend components.",
                            role_required="frontend-engineer",
                            skills_required=["React", "Next.js", "TypeScript"],
                            execution_mode=ExecutionMode.TERMINAL,
                            dependencies=["task-api-02"],
                            acceptance_criteria="Responsive web client connected to API gateway.",
                            subtasks=[
                                "Build React components for dashboard view",
                                "Wire client API fetching and live WebSocket status updates",
                            ],
                            estimated_hours=4,
                            max_tokens=50000,
                        ),
                    ],
                ),
                PlannedPhase(
                    title="Independent Verification, Adversarial QA & Hardening",
                    tasks=[
                        PlannedTask(
                            task_id="task-qa-05",
                            title="Adversarial End-to-End & Security Audit",
                            objective="Audit end-to-end security and run test suites.",
                            scope="QA and security auditing with end-to-end test execution.",
                            role_required="qa-engineer",
                            skills_required=["pytest", "Playwright", "API Testing"],
                            execution_mode=ExecutionMode.HYBRID,
                            dependencies=["task-logic-03", "task-ui-04"],
                            acceptance_criteria="Zero high-severity vulnerabilities, all e2e test suites passing green.",
                            subtasks=[
                                "Execute automated pytest test suites covering edge cases and invalid payloads",
                                "Run security scanner and verify non-root container isolation",
                            ],
                            estimated_hours=4,
                            max_tokens=50000,
                        ),
                    ],
                ),
            ],
        )

        validate_plan_dag(canonical_fallback)

        phase_dicts: list[dict[str, Any]] = []
        for phase_index, phase in enumerate(canonical_fallback.phases, start=1):
            tasks = []
            for task_index, task in enumerate(phase.tasks, start=1):
                tasks.append({
                    "task_id": f"TASK-{phase_index:02d}-{task_index:02d}-{uuid.uuid4().hex[:4].upper()}",
                    "source_task_id": task.task_id,
                    "title": task.title,
                    "objective": task.objective or task.title,
                    "scope": task.scope,
                    "role_required": task.role_required,
                    "skills_required": list(task.skills_required),
                    "execution_mode": task.execution_mode.value,
                    "dependencies": list(task.dependencies),
                    "acceptance_criteria": task.acceptance_criteria,
                    "subtasks": list(task.subtasks),
                    "resource_budget": {
                        "estimated_hours": task.estimated_hours,
                        "max_tokens": task.max_tokens,
                    },
                })
            phase_dicts.append({
                "phase_id": f"PHASE-{phase_index:02d}",
                "title": phase.title,
                "tasks": tasks,
            })

        id_map = {
            task["source_task_id"]: task["task_id"]
            for phase in phase_dicts for task in phase["tasks"]
        }
        for phase in phase_dicts:
            for task in phase["tasks"]:
                task["dependencies"] = [id_map[value] for value in task["dependencies"]]

        return {
            "code_name": canonical_fallback.code_name,
            "complexity": canonical_fallback.complexity,
            "requirements": canonical_fallback.requirements,
            "risk_analysis": {
                "technical_risks": canonical_fallback.technical_risks,
                "mitigation_strategies": canonical_fallback.mitigation_strategies,
            },
            "clarification_required": False,
            "clarifying_questions": [],
            "plan_source": "rules_fallback",
            "phases": phase_dicts,
        }


