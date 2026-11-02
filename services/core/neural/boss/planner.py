import re
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from neural.exceptions import ModelProviderError
from neural.llm.gateway import LLMGateway


class PlannedTask(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=3, max_length=180)
    objective: str = Field(default="", max_length=2000)
    scope: str = Field(min_length=1, max_length=2000)
    role_required: str = Field(min_length=1, max_length=100)
    skills_required: list[str] = Field(min_length=1, max_length=12)
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
    requirements: list[str] = Field(min_length=1, max_length=30)
    technical_risks: list[str] = Field(default_factory=list, max_length=20)
    mitigation_strategies: list[str] = Field(default_factory=list, max_length=20)
    phases: list[PlannedPhase] = Field(min_length=1, max_length=12)


class TaskPlanner:
    """
    Hierarchical Task Decomposition Engine.
    Breaks high-level objectives into Projects -> Phases -> Tasks -> Subtasks
    with explicit dependency DAGs, requirement extraction, and risk analysis.
    """

    def __init__(self, llm_gateway: Optional[LLMGateway] = None) -> None:
        self.llm_gateway = llm_gateway

    @staticmethod
    def _has_configured_provider(gateway: LLMGateway) -> bool:
        if gateway.custom_invoker is not None:
            return True
        settings = gateway.settings
        return settings.BOSS_LLM_PLANNING_ENABLED and any(
            getattr(settings, name, None)
            for name in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
        )

    async def _decompose_with_model(self, objective: str) -> dict[str, Any]:
        assert self.llm_gateway is not None
        plan, response = await self.llm_gateway.generate_structured(
            prompt=(
                "Create a practical software project plan for this human objective. "
                "Use 2-6 phases and concrete tasks an engineering team can deliver. "
                "Every task needs measurable acceptance criteria and required skills. "
                "Dependencies must reference task_id values in this plan, point only "
                "to earlier tasks, and form an acyclic graph. Do not claim work is "
                "already completed.\n\nOBJECTIVE:\n" + objective
            ),
            response_model=ObjectivePlan,
            route="boss_brain",
            system_prompt="You are ANCOS Boss. Plan only; never report unperformed work as complete.",
            temperature=0.1,
            max_tokens=3500,
        )

        raw_ids = [task.task_id for phase in plan.phases for task in phase.tasks]
        if len(raw_ids) != len(set(raw_ids)):
            raise ModelProviderError("Boss plan rejected: duplicate task IDs.")
        known_ids = set(raw_ids)
        seen_ids: set[str] = set()
        for phase in plan.phases:
            for task in phase.tasks:
                if task.task_id in task.dependencies or any(
                    dependency not in known_ids or dependency not in seen_ids
                    for dependency in task.dependencies
                ):
                    raise ModelProviderError(
                        f"Boss plan rejected: invalid or forward dependency in {task.task_id}."
                    )
                seen_ids.add(task.task_id)

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
            "requirements": plan.requirements,
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

    async def decompose_objective(self, objective: str) -> dict[str, Any]:
        """
        Decomposes natural language objective into hierarchical execution plan:
        Projects -> Phases -> Tasks -> Subtasks
        """
        if objective and objective.strip() and self.llm_gateway and self._has_configured_provider(self.llm_gateway):
            return await self._decompose_with_model(objective.strip())

        words = re.findall(r"\b[A-Za-z0-9]+\b", objective) if objective else []
        code_name = words[0].capitalize() if words else "Omega"
        complexity = self.score_complexity(objective)
        requirements = self.extract_requirements(objective)
        risk_analysis = self.assess_risks(objective)

        return {
            "code_name": code_name,
            "complexity": complexity,
            "requirements": requirements,
            "risk_analysis": risk_analysis,
            "plan_source": "rules_fallback",
            "phases": [
                {
                    "phase_id": "PHASE-01",
                    "title": "System Architecture & Foundation",
                    "tasks": [
                        {
                            "task_id": f"TASK-{uuid.uuid4().hex[:6].upper()}",
                            "title": "Domain Modeling & Database Schema Design",
                            "role_required": "senior-backend-engineer",
                            "skills_required": ["System Design", "PostgreSQL"],
                            "dependencies": [],
                            "acceptance_criteria": "Fully normalized schema with migrations and foreign key integrity.",
                            "subtasks": [
                                "Define SQLAlchemy ORM models with explicit foreign key constraints",
                                "Configure database migrations and connection pool settings"
                            ],
                            "resource_budget": {"estimated_hours": 3, "max_tokens": 40000}
                        },
                        {
                            "task_id": f"TASK-{uuid.uuid4().hex[:6].upper()}",
                            "title": "API Gateway & Security Specification",
                            "role_required": "senior-backend-engineer",
                            "skills_required": ["FastAPI", "REST APIs"],
                            "dependencies": [],
                            "acceptance_criteria": "FastAPI routes defined with OpenAPI spec and JWT auth contracts.",
                            "subtasks": [
                                "Create FastAPI route definitions with typed Pydantic request/response models",
                                "Implement authentication dependency and error handling middleware"
                            ],
                            "resource_budget": {"estimated_hours": 3, "max_tokens": 40000}
                        }
                    ]
                },
                {
                    "phase_id": "PHASE-02",
                    "title": "Parallel Service Implementation & UI",
                    "tasks": [
                        {
                            "task_id": f"TASK-{uuid.uuid4().hex[:6].upper()}",
                            "title": "Core Business Logic Services",
                            "role_required": "senior-backend-engineer",
                            "skills_required": ["Python", "FastAPI"],
                            "dependencies": ["PHASE-01"],
                            "acceptance_criteria": "Business logic implemented with unit test coverage > 90%.",
                            "subtasks": [
                                "Implement core business service functions and repositories",
                                "Integrate transactional boundaries and logging"
                            ],
                            "resource_budget": {"estimated_hours": 5, "max_tokens": 60000}
                        },
                        {
                            "task_id": f"TASK-{uuid.uuid4().hex[:6].upper()}",
                            "title": "Interactive Client Dashboard & State",
                            "role_required": "frontend-engineer",
                            "skills_required": ["React", "Next.js", "TypeScript"],
                            "dependencies": ["PHASE-01"],
                            "acceptance_criteria": "Responsive web client connected to API gateway.",
                            "subtasks": [
                                "Build React components for dashboard view",
                                "Wire client API fetching and live WebSocket status updates"
                            ],
                            "resource_budget": {"estimated_hours": 4, "max_tokens": 50000}
                        }
                    ]
                },
                {
                    "phase_id": "PHASE-03",
                    "title": "Independent Verification, Adversarial QA & Hardening",
                    "tasks": [
                        {
                            "task_id": f"TASK-{uuid.uuid4().hex[:6].upper()}",
                            "title": "Adversarial End-to-End & Security Audit",
                            "role_required": "qa-engineer",
                            "skills_required": ["pytest", "Playwright", "API Testing"],
                            "dependencies": ["PHASE-02"],
                            "acceptance_criteria": "Zero high-severity vulnerabilities, all e2e test suites passing green.",
                            "subtasks": [
                                "Execute automated pytest test suites covering edge cases and invalid payloads",
                                "Run security scanner and verify non-root container isolation"
                            ],
                            "resource_budget": {"estimated_hours": 4, "max_tokens": 50000}
                        }
                    ]
                }
            ]
        }


