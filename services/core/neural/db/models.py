import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from neural.constants import (
    ActionItemStatus,
    ApprovalStatus,
    ContainerStatus,
    EmployeeStatus,
    MeetingStatus,
    MeetingType,
    MemoryCategory,
    ProjectStatus,
    ProposalStatus,
    ProposalType,
    SkillLevel,
    TaskPriority,
    TaskStatus,
    VerificationStatus,
    VoteChoice,
)
from neural.db.engine import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        SQLEnum(ProjectStatus), default=ProjectStatus.PLANNING, nullable=False
    )
    execution_plan: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )

    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="project", cascade="all, delete-orphan")
    artifacts: Mapped[list["Artifact"]] = relationship("Artifact", back_populates="project")
    proposals: Mapped[list["Proposal"]] = relationship("Proposal", back_populates="project")


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    employee_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    department: Mapped[str] = mapped_column(String(128), default="Engineering", nullable=False)
    personality: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    communication_style: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model_config_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[EmployeeStatus] = mapped_column(
        SQLEnum(EmployeeStatus), default=EmployeeStatus.IDLE, nullable=False
    )
    performance_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    hired_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    container_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("containers.id"), nullable=True
    )
    container: Mapped[Optional["Container"]] = relationship("Container", back_populates="employee")

    skills: Mapped[list["EmployeeSkill"]] = relationship(
        "EmployeeSkill", back_populates="employee", cascade="all, delete-orphan"
    )
    certifications: Mapped[list["SkillCertification"]] = relationship(
        "SkillCertification", back_populates="employee", cascade="all, delete-orphan", lazy="selectin"
    )
    assigned_tasks: Mapped[list["Task"]] = relationship("Task", back_populates="assignee")
    performance_records: Mapped[list["PerformanceRecord"]] = relationship(
        "PerformanceRecord", back_populates="employee"
    )


class EmployeeSkill(Base):
    __tablename__ = "employee_skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    level: Mapped[SkillLevel] = mapped_column(
        SQLEnum(SkillLevel), default=SkillLevel.INTERMEDIATE, nullable=False
    )
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    evidence_task_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )

    employee: Mapped["Employee"] = relationship("Employee", back_populates="skills")


class SkillCertification(Base):
    __tablename__ = "skill_certifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    certificate_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    level: Mapped[SkillLevel] = mapped_column(SQLEnum(SkillLevel), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="certified", nullable=False)
    benchmark_id: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_receipt: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rubric_scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    certificate_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    employee: Mapped["Employee"] = relationship("Employee", back_populates="certifications")



class Container(Base):
    __tablename__ = "containers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    docker_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ContainerStatus] = mapped_column(
        SQLEnum(ContainerStatus), default=ContainerStatus.PROVISIONING, nullable=False
    )
    vnc_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    novnc_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vnc_password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resource_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    network_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    workspace_path: Mapped[str] = mapped_column(String(512), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    employee: Mapped[Optional["Employee"]] = relationship("Employee", back_populates="container", uselist=False)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    task_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    parent_task_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=True
    )
    assigned_to: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("employees.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    required_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority: Mapped[TaskPriority] = mapped_column(
        SQLEnum(TaskPriority), default=TaskPriority.MEDIUM, nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        SQLEnum(TaskStatus), default=TaskStatus.PENDING, nullable=False
    )
    resource_budget: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification_result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="tasks")
    assignee: Mapped[Optional["Employee"]] = relationship("Employee", back_populates="assigned_tasks")
    subtasks: Mapped[list["Task"]] = relationship(
        "Task", backref="parent_task", remote_side=[id]
    )
    dependencies: Mapped[list["TaskDependency"]] = relationship(
        "TaskDependency", foreign_keys="TaskDependency.task_id", back_populates="task"
    )
    results: Mapped[list["TaskResult"]] = relationship("TaskResult", back_populates="task")
    artifacts: Mapped[list["Artifact"]] = relationship("Artifact", back_populates="task")


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_number", name="uq_task_attempt_number"),
        UniqueConstraint("task_id", "fencing_token", name="uq_task_attempt_fencing_token"),
        Index("ix_task_attempt_lease", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="leased", index=True, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    execution_receipt: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    dependency_type: Mapped[str] = mapped_column(String(64), default="blocks")

    task: Mapped["Task"] = relationship("Task", foreign_keys=[task_id], back_populates="dependencies")


class TaskResult(Base):
    __tablename__ = "task_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    output_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list[str]] = mapped_column(JSON, default=list)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        SQLEnum(VerificationStatus), default=VerificationStatus.PENDING, nullable=False
    )
    verification_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    task: Mapped["Task"] = relationship("Task", back_populates="results")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)  # code, doc, report, binary
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    project: Mapped["Project"] = relationship("Project", back_populates="artifacts")
    task: Mapped[Optional["Task"]] = relationship("Task", back_populates="artifacts")


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    meeting_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    meeting_type: Mapped[MeetingType] = mapped_column(SQLEnum(MeetingType), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[MeetingStatus] = mapped_column(
        SQLEnum(MeetingStatus), default=MeetingStatus.SCHEDULED, nullable=False
    )
    participants: Mapped[list[str]] = mapped_column(JSON, default=list)
    agenda: Mapped[list[str]] = mapped_column(JSON, default=list)
    minutes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    concluded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    action_items: Mapped[list["ActionItem"]] = relationship(
        "ActionItem", back_populates="meeting", cascade="all, delete-orphan"
    )


class ActionItem(Base):
    __tablename__ = "action_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    meeting_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_to: Mapped[str] = mapped_column(String(64), nullable=False)  # employee_id or Boss
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[ActionItemStatus] = mapped_column(
        SQLEnum(ActionItemStatus), default=ActionItemStatus.OPEN, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    meeting: Mapped["Meeting"] = relationship("Meeting", back_populates="action_items")


class PerformanceRecord(Base):
    __tablename__ = "performance_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    employee_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    completion_time_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_score: Mapped[float] = mapped_column(Float, default=1.0)
    cost_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(32), default="success")  # success, failure
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    employee: Mapped["Employee"] = relationship("Employee", back_populates="performance_records")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    action_type: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        SQLEnum(ApprovalStatus), default=ApprovalStatus.PENDING, nullable=False
    )
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MemoryEntry(Base):
    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    category: Mapped[MemoryCategory] = mapped_column(SQLEnum(MemoryCategory), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    embedding_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class EvolutionRecord(Base):
    __tablename__ = "evolution_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    record_type: Mapped[str] = mapped_column(String(64), nullable=False)  # prompt, tool, workflow, model
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    experiment_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    experiment_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="in_review")  # approved, rejected, rolled_back
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    request_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    signature: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    previous_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)



class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    message_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    sender: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    recipient: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    department: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_delivery_ready", "status", "available_at", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True, nullable=False)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    proposal_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_type: Mapped[ProposalType] = mapped_column(
        SQLEnum(ProposalType), default=ProposalType.STRATEGIC_INITIATIVE, nullable=False
    )
    status: Mapped[ProposalStatus] = mapped_column(
        SQLEnum(ProposalStatus), default=ProposalStatus.DRAFT, nullable=False
    )
    proposer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    proposer_role: Mapped[str] = mapped_column(String(128), default="BossAI", nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deliberation_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    quorum_threshold: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    approval_threshold: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    execution_receipt: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="proposals")
    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="proposal", cascade="all, delete-orphan", lazy="selectin")
    ledger_entries: Mapped[list["DecisionLedgerEntry"]] = relationship("DecisionLedgerEntry", back_populates="proposal", cascade="all, delete-orphan", lazy="selectin", order_by="DecisionLedgerEntry.sequence_number")

    @property
    def ledger_entry(self) -> Optional["DecisionLedgerEntry"]:
        if "ledger_entries" in self.__dict__ and self.ledger_entries:
            return self.ledger_entries[-1]
        return None


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("proposal_id", "voter_id", name="uq_proposal_voter"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    proposal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    voter_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    voter_name: Mapped[str] = mapped_column(String(128), nullable=False)
    voter_role: Mapped[str] = mapped_column(String(128), nullable=False)
    department: Mapped[str] = mapped_column(String(128), default="Executive", nullable=False)
    choice: Mapped[VoteChoice] = mapped_column(SQLEnum(VoteChoice), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    voted_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    proposal: Mapped["Proposal"] = relationship("Proposal", back_populates="votes")


class DecisionLedgerEntry(Base):
    __tablename__ = "decision_ledger"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    entry_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    proposal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("proposals.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    proposal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    proposer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    votes_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    execution_receipt: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)

    proposal: Mapped["Proposal"] = relationship("Proposal", back_populates="ledger_entries", lazy="selectin")


class ToolRecord(Base):
    __tablename__ = "tool_registry"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_tool_name_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    tool_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="utility", index=True, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    test_code: Mapped[str] = mapped_column(Text, nullable=False)
    interface_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    author_id: Mapped[str] = mapped_column(String(128), default="autonomous_toolsmith", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True, nullable=False)
    safety_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    safety_violations: Mapped[list[str]] = mapped_column(JSON, default=list)
    verification_receipt: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )


class MethodRecord(Base):
    __tablename__ = "method_records"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_method_name_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    method_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    domain: Mapped[str] = mapped_column(String(128), default="engineering", index=True, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    fallback_strategy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True, nullable=False)
    baseline_method_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    author_id: Mapped[str] = mapped_column(String(128), default="MethodLabAI", nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, onupdate=utc_now
    )

    benchmarks: Mapped[list["MethodBenchmarkRecord"]] = relationship(
        "MethodBenchmarkRecord", back_populates="method", cascade="all, delete-orphan", lazy="selectin"
    )


class MethodBenchmarkRecord(Base):
    __tablename__ = "method_benchmarks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    benchmark_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    method_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("method_records.id", ondelete="CASCADE"), index=True, nullable=False
    )
    baseline_method_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    total_scenarios: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed_scenarios: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    accuracy: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    token_efficiency: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    reproduction_rate: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    baseline_accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_token_efficiency: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    relative_improvement_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(32), default="iterate", nullable=False)
    scenario_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    method: Mapped["MethodRecord"] = relationship("MethodRecord", back_populates="benchmarks")


class DecisionRecord(Base):
    __tablename__ = "decision_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    decision_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)  # CHOICE, SCORE, NOUL
    model_checkpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_choice: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    probabilities: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    calibrated_prob: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    was_escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    shadow_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    actual_outcome: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)






