from enum import Enum


class ExecutionMode(str, Enum):
    GUI = "gui"
    GUI_ONLY = "gui_only"
    TERMINAL = "terminal"
    TERMINAL_ONLY = "terminal_only"
    HYBRID = "hybrid"
    AUTHORIZED_TOOLS = "authorized_tools"
    RESEARCH_REASONING = "research_reasoning"

    @classmethod
    def is_terminal(cls, mode: object) -> bool:
        if mode is None:
            return False
        val = getattr(mode, "value", mode)
        return val in {cls.TERMINAL.value, cls.TERMINAL_ONLY.value}

    @classmethod
    def is_gui(cls, mode: object) -> bool:
        if mode is None:
            return False
        val = getattr(mode, "value", mode)
        return val in {cls.GUI.value, cls.GUI_ONLY.value}

    @classmethod
    def normalize(cls, mode: object) -> "ExecutionMode":
        if mode is None:
            return cls.TERMINAL_ONLY
        val = str(getattr(mode, "value", mode)).lower()
        if val in {"terminal", "terminal_only"}:
            return cls.TERMINAL_ONLY
        if val in {"gui", "gui_only"}:
            return cls.GUI_ONLY
        if val == "hybrid":
            return cls.HYBRID
        if val in {"authorized_tools", "tools"}:
            return cls.AUTHORIZED_TOOLS
        if val in {"research_reasoning", "research", "reasoning"}:
            return cls.RESEARCH_REASONING
        return cls(val)


ALL_EXECUTION_MODES: list[ExecutionMode] = [
    ExecutionMode.TERMINAL_ONLY,
    ExecutionMode.GUI_ONLY,
    ExecutionMode.HYBRID,
    ExecutionMode.AUTHORIZED_TOOLS,
    ExecutionMode.RESEARCH_REASONING,
]


class ExecutionCapability(str, Enum):
    """Concrete execution environment an employee must possess to run a task.

    Modes are declarative intent; capabilities are the actual environments a
    worker/employee can operate. Keeping them separate lets routing match a
    task to an employee that truly has the required environment instead of a
    single hardcoded mode string.
    """

    TERMINAL = "terminal"
    GUI = "gui"


_MODE_CAPABILITIES: dict[ExecutionMode, frozenset[ExecutionCapability]] = {
    ExecutionMode.TERMINAL: frozenset({ExecutionCapability.TERMINAL}),
    ExecutionMode.TERMINAL_ONLY: frozenset({ExecutionCapability.TERMINAL}),
    ExecutionMode.GUI: frozenset({ExecutionCapability.GUI}),
    ExecutionMode.GUI_ONLY: frozenset({ExecutionCapability.GUI}),
    ExecutionMode.HYBRID: frozenset({ExecutionCapability.GUI, ExecutionCapability.TERMINAL}),
    ExecutionMode.AUTHORIZED_TOOLS: frozenset({ExecutionCapability.TERMINAL}),
    ExecutionMode.RESEARCH_REASONING: frozenset({ExecutionCapability.TERMINAL}),
}


def required_execution_capabilities(mode: object) -> frozenset[ExecutionCapability]:
    """Returns the execution capabilities a task in ``mode`` requires."""
    return _MODE_CAPABILITIES.get(ExecutionMode.normalize(mode), frozenset({ExecutionCapability.TERMINAL}))


def capabilities_cover_mode(declared_capabilities: object, mode: object) -> bool:
    """True when a declared capability set covers every capability ``mode`` needs.

    An empty declaration is treated as terminal-capable for backward
    compatibility with employees created before capabilities existed.
    """
    required = required_execution_capabilities(mode)
    declared = {
        str(capability).lower()
        for capability in (declared_capabilities or [])
    }
    if not declared:
        declared = {ExecutionCapability.TERMINAL.value}
    return all(capability.value in declared for capability in required)


class ExecutionTarget(str, Enum):
    """Concrete environment a task step must be executed against.

    Modes are declarative intent; targets are the actual environments required.
    A terminal task needs only an execution environment; a GUI task additionally
    needs a live graphical desktop. Keeping them separate is what lets terminal
    work run without a desktop.
    """

    TERMINAL = "terminal"
    GUI = "gui"


def required_execution_targets(mode: object) -> frozenset[ExecutionTarget]:
    """Return the execution targets a task in ``mode`` genuinely requires."""
    capabilities = required_execution_capabilities(mode)
    targets: set[ExecutionTarget] = set()
    if ExecutionCapability.TERMINAL in capabilities:
        targets.add(ExecutionTarget.TERMINAL)
    if ExecutionCapability.GUI in capabilities:
        targets.add(ExecutionTarget.GUI)
    return frozenset(targets)


def mode_requires_gui(mode: object) -> bool:
    """True when a task in ``mode`` requires a live graphical desktop."""
    return ExecutionTarget.GUI in required_execution_targets(mode)


def mode_requires_terminal(mode: object) -> bool:
    """True when a task in ``mode`` requires a terminal execution environment.

    Hybrid tasks run a terminal phase as well as a GUI phase, so terminal
    endpoints (worker plan/exec, verifier exec/manifest) must accept them. Gating
    those endpoints on ``ExecutionMode.is_terminal`` wrongly rejected hybrid and
    stranded every hybrid task before its first command.
    """
    return ExecutionTarget.TERMINAL in required_execution_targets(mode)


_TERMINAL_MODES: frozenset[ExecutionMode] = frozenset(
    {ExecutionMode.TERMINAL, ExecutionMode.TERMINAL_ONLY}
)
_GUI_MODES: frozenset[ExecutionMode] = frozenset(
    {ExecutionMode.GUI, ExecutionMode.GUI_ONLY, ExecutionMode.HYBRID}
)


def required_skills_satisfied(employee: object, required_capabilities: object) -> bool:
    """True when the employee's skills intersect the task's required skills.

    A task with no ``required_capabilities`` imposes no skill gate. When
    requirements exist, the worker claim route refuses the task unless at least
    one of the assignee's declared skills matches. Every assignment path must
    apply the identical rule, or it can pin a task to an employee that can never
    claim it.
    """
    req_set = {
        str(skill).strip().lower()
        for skill in (required_capabilities or [])
        if str(skill).strip()
    }
    if not req_set:
        return True
    emp_skills = {
        str(skill.skill_name).strip().lower()
        for skill in (getattr(employee, "skills", None) or [])
    }
    return bool(req_set.intersection(emp_skills))


def worker_declared_modes(
    worker_id: str,
    configured: dict[str, object] | None = None,
) -> frozenset[ExecutionMode] | None:
    """Resolve the execution modes a worker identity is allowed to run.

    Worker identity comes from an authenticated bearer token, so it is the only
    trustworthy statement of *what a worker is*. An explicit entry in
    ``configured`` wins; otherwise the identity name is used as a hint. A worker
    whose name names no mode family is treated as unrestricted (``None``) so
    generic single-mode workers keep their previous behaviour.
    """
    worker_id = (worker_id or "").strip()
    if configured and worker_id in configured:
        declared = configured[worker_id]
        if isinstance(declared, str):
            declared = [part for part in declared.replace(",", " ").split() if part]
        try:
            return frozenset(ExecutionMode.normalize(mode) for mode in (declared or []))
        except ValueError:
            return None
    name = worker_id.lower()
    if "gui" in name:
        return _GUI_MODES
    if "terminal" in name:
        return _TERMINAL_MODES
    return None


class ProjectStatus(str, Enum):
    PLANNING = "planning"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EmployeeStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


class SkillLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"
    # Progression tiers (novice, competent, expert, master)
    NOVICE = "novice"
    COMPETENT = "competent"
    MASTER = "master"


SKILL_PROGRESSION_ORDER: list[SkillLevel] = [
    SkillLevel.NOVICE,
    SkillLevel.COMPETENT,
    SkillLevel.EXPERT,
    SkillLevel.MASTER,
]

SKILL_LEVEL_RANK: dict[str, int] = {
    "beginner": 1,
    "novice": 1,
    "intermediate": 2,
    "competent": 2,
    "advanced": 3,
    "expert": 3,
    "master": 4,
}



class MeetingType(str, Enum):
    DAILY_STANDUP = "daily_standup"
    PROJECT_REVIEW = "project_review"
    DEPARTMENT_MEETING = "department_meeting"
    ARCHITECTURE_REVIEW = "architecture_review"
    INCIDENT_REVIEW = "incident_review"
    PERFORMANCE_REVIEW = "performance_review"
    HIRING_PLANNING = "hiring_planning"
    EVOLUTION_STRATEGY = "evolution_strategy"
    EXECUTIVE_BRIEFING = "executive_briefing"
    BOARDROOM_DELIBERATION = "boardroom_deliberation"
    STRATEGIC_GOVERNANCE = "strategic_governance"
    AD_HOC = "ad_hoc"


class MeetingStatus(str, Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    CONCLUDED = "concluded"
    CANCELLED = "cancelled"


class ActionItemStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ContainerStatus(str, Enum):
    PROVISIONING = "provisioning"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


class MemoryCategory(str, Enum):
    POLICY = "policy"
    DECISION = "decision"
    LESSON = "lesson"
    PROCEDURE = "procedure"
    KNOWLEDGE = "knowledge"
    EPISODE = "episode"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    DISCUSSION = "discussion"
    VOTING = "voting"
    APPROVED = "approved"
    REJECTED = "rejected"
    VETOED = "vetoed"
    EXECUTED = "executed"
    CANCELLED = "cancelled"


class ProposalType(str, Enum):
    STRATEGIC_INITIATIVE = "strategic_initiative"
    ARCHITECTURE_CHANGE = "architecture_change"
    BUDGET_ALLOCATION = "budget_allocation"
    POLICY_UPDATE = "policy_update"
    EMERGENCY_ACTION = "emergency_action"
    TASK_ORCHESTRATION = "task_orchestration"


class VoteChoice(str, Enum):
    AYE = "aye"
    NAY = "nay"
    ABSTAIN = "abstain"


class SecurityRole(str, Enum):
    OWNER = "owner"
    BOSS = "boss"
    WORKER = "worker"
    VERIFIER = "verifier"
    SECURITY_REVIEWER = "security_reviewer"


class CommitteeRole(str, Enum):
    CHAIR = "chair"
    HEAD_OF_ENGINEERING = "head_of_engineering"
    HEAD_OF_PRODUCT = "head_of_product"
    HEAD_OF_QA = "head_of_qa"
    HEAD_OF_SECURITY = "head_of_security"
    HEAD_OF_INFRASTRUCTURE = "head_of_infrastructure"
    HEAD_OF_FINANCE = "head_of_finance"
    MEMBER = "member"


class DestructiveOperationType(str, Enum):
    FILE_DELETION = "file_deletion"
    PRODUCTION_DEPLOYMENT = "production_deployment"
    DATABASE_MIGRATION = "database_migration"
    CREDENTIAL_ROTATION = "credential_rotation"
    INFRASTRUCTURE_DESTROY = "infrastructure_destroy"
    BRANCH_MERGE = "branch_merge"
    SELF_EVOLUTION_PROMOTION = "self_evolution_promotion"


class DecisionType(str, Enum):
    CHOICE = "CHOICE"
    SCORE = "SCORE"
    NOUL = "NOUL"


class DecisionRiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ModelRuntime(str, Enum):
    ONNX = "onnx"
    TORCH = "torch"
    FALLBACK = "fallback"


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"




