import re
import uuid
from typing import Any


class TaskPlanner:
    """
    Hierarchical Task Decomposition Engine.
    Breaks high-level objectives into Projects -> Phases -> Tasks -> Subtasks
    with explicit dependency DAGs, requirement extraction, and risk analysis.
    """

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


