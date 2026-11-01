from enum import Enum


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




