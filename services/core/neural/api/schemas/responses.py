from datetime import datetime
from typing import Any, Optional

from neural.constants import (
    ActionItemStatus,
    EmployeeStatus,
    ExecutionMode,
    MeetingStatus,
    MeetingType,
    ProjectStatus,
    ProposalStatus,
    ProposalType,
    TaskStatus,
    VoteChoice,
)
from pydantic import BaseModel, Field


class ProjectResponse(BaseModel):
    id: str
    name: str
    objective: str
    status: ProjectStatus
    execution_plan: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class BossTaskAttemptReport(BaseModel):
    attempt_number: int
    worker_id: str
    status: str
    fencing_token: int
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_message: Optional[str] = None
    verifier_id: Optional[str] = None
    verification_passed: Optional[bool] = None
    verification_notes: Optional[str] = None


class BossTaskReport(BaseModel):
    task_id: str
    title: str
    objective: str
    acceptance_criteria: str
    execution_mode: ExecutionMode
    status: TaskStatus
    retry_count: int = 0
    max_retries: int = 3
    priority: str
    assigned_employee_id: Optional[str] = None
    assigned_employee_name: Optional[str] = None
    deadline: Optional[datetime] = None
    overdue: bool = False
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    verified: bool = False
    verification_notes: Optional[str] = None
    evidence_refs: list[str] = Field(default_factory=list)
    attempts: list[BossTaskAttemptReport] = Field(default_factory=list)


class BossProjectReport(BaseModel):
    project_id: str
    project_name: str
    objective: str
    project_status: ProjectStatus
    generated_at: datetime
    task_counts: dict[str, int]
    tasks: list[BossTaskReport]


class TaskResponse(BaseModel):
    id: str
    task_id: str
    project_id: str
    title: str
    execution_mode: ExecutionMode
    status: TaskStatus
    assigned_to: Optional[str]
    priority: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class EmployeeResponse(BaseModel):
    id: str
    employee_id: str
    name: str
    title: str
    department: str
    status: EmployeeStatus
    skills: list[dict[str, Any]] = []
    hired_at: datetime
    container_id: Optional[str] = None
    desktop_url: Optional[str] = None


class WorkstationResponse(BaseModel):
    docker_id: str
    employee_id: str
    image: str
    vnc_port: int
    novnc_port: int
    desktop_url: str
    cpu_limit: float
    mem_limit: str
    status: str
    workspace: str
    started_at: Optional[str] = None
    vnc_password: Optional[str] = Field(default=None, repr=False)


class VncCredentialResponse(BaseModel):
    employee_id: str
    docker_id: str
    vnc_password: str = Field(repr=False)



class ActionItemResponse(BaseModel):
    id: str
    meeting_id: str
    description: str
    assigned_to: str
    deadline: Optional[datetime] = None
    status: ActionItemStatus
    created_at: datetime
    is_overdue: bool = False


class MeetingResponse(BaseModel):
    id: str
    meeting_id: str
    title: str
    meeting_type: MeetingType
    status: MeetingStatus
    scheduled_at: datetime
    participants: list[str]
    agenda: list[str]
    minutes: dict[str, Any]


class MeetingDetailResponse(BaseModel):
    id: str
    meeting_id: str
    title: str
    meeting_type: MeetingType
    status: MeetingStatus
    scheduled_at: datetime
    started_at: Optional[datetime] = None
    concluded_at: Optional[datetime] = None
    participants: list[str]
    agenda: list[str]
    minutes: dict[str, Any]
    decisions: list[dict[str, Any]] = []
    action_items: list[ActionItemResponse] = []


class ExecutiveBriefingResponse(BaseModel):
    generated_at: str
    executive_summary: str
    milestones: list[dict[str, Any]]
    active_workforce: dict[str, Any]
    blockers: list[dict[str, Any]]
    financial_costs: dict[str, Any]
    pending_approvals: list[dict[str, Any]]
    action_items_summary: dict[str, Any]


class MessageResponse(BaseModel):
    id: str
    message_id: str
    message_type: str
    channel: str
    sender: str
    recipient: Optional[str] = None
    department: Optional[str] = None
    correlation_id: Optional[str] = None
    payload: dict[str, Any] = {}
    created_at: datetime


class LLMTelemetryResponse(BaseModel):
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: float
    by_model: dict[str, Any] = {}


class TaskDAGResponse(BaseModel):
    project_id: str
    nodes: list[dict[str, Any]]
    edges: list[tuple[str, str]]
    topological_order: list[str]
    critical_path: list[str]
    ready_tasks: list[str]


class ExecuteDagResponse(BaseModel):
    project_id: str
    total_tasks: int
    completed: list[str]
    failed: list[str]
    blocked: list[str]
    success: bool


class VoteResponse(BaseModel):
    id: str
    proposal_id: str
    voter_id: str
    voter_name: str
    voter_role: str
    department: str
    choice: VoteChoice
    weight: float
    rationale: Optional[str] = None
    voted_at: datetime


class ProposalResponse(BaseModel):
    id: str
    proposal_id: str
    title: str
    description: str
    proposal_type: ProposalType
    status: ProposalStatus
    proposer_id: str
    proposer_role: str
    project_id: Optional[str] = None
    quorum_threshold: float
    approval_threshold: float
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    votes_count: int = 0


class ProposalDetailResponse(BaseModel):
    id: str
    proposal_id: str
    title: str
    description: str
    proposal_type: ProposalType
    status: ProposalStatus
    proposer_id: str
    proposer_role: str
    project_id: Optional[str] = None
    parameters: dict[str, Any] = {}
    deliberation_history: list[dict[str, Any]] = []
    quorum_threshold: float
    approval_threshold: float
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    execution_receipt: Optional[dict[str, Any]] = None
    votes: list[VoteResponse] = []
    ledger_entry: Optional[dict[str, Any]] = None


class DecisionLedgerEntryResponse(BaseModel):
    id: str
    entry_id: str
    proposal_id: str
    sequence_number: int
    decision: str
    title: str
    proposal_type: str
    proposer_id: str
    votes_summary: dict[str, Any] = {}
    rationale: str
    execution_receipt: Optional[dict[str, Any]] = None
    previous_hash: str
    entry_hash: str
    recorded_at: datetime


class LedgerVerificationResponse(BaseModel):
    is_valid: bool
    total_entries: int
    latest_sequence: int
    error: Optional[str] = None
    verified_at: datetime


class CommitteeMemberResponse(BaseModel):
    member_id: str
    name: str
    role: str
    department: str
    voting_weight: float
    is_active: bool


class SkillCatalogItemResponse(BaseModel):
    skill_id: str
    name: str
    category: str
    description: str
    grading_rubric: dict[str, Any]
    available_levels: list[str]


class BenchmarkItemResponse(BaseModel):
    benchmark_id: str
    skill_name: str
    level: str
    title: str
    description: str
    test_cases_count: int


class CertificationResponse(BaseModel):
    passed: bool
    certificate_id: Optional[str] = None
    certificate_hash: Optional[str] = None
    skill_name: str
    target_level: str
    employee_id: str
    score: float
    rubric_evaluation: dict[str, Any]
    receipt: dict[str, Any]
    notes: str


class CertificateDetailResponse(BaseModel):
    id: str
    certificate_id: str
    employee_id: str
    skill_name: str
    level: str
    score: float
    status: str
    benchmark_id: str
    certificate_hash: str
    execution_receipt: dict[str, Any]
    rubric_scores: dict[str, Any]
    issued_at: datetime
    expires_at: Optional[datetime] = None


class CertificateVerificationResponse(BaseModel):
    certificate_id: str
    is_valid: bool
    message: str
    certificate_hash: str
    verified_at: datetime


class SkillMatrixResponse(BaseModel):
    total_employees: int
    skills_catalog: list[str]
    matrix: list[dict[str, Any]]
    coverage: dict[str, Any]


class EmployeeProgressionResponse(BaseModel):
    employee_id: str
    name: str
    title: str
    department: str
    status: str
    hired_at: Optional[str] = None
    skills: list[dict[str, Any]]
    performance_summary: dict[str, Any]


class SkillGapReportResponse(BaseModel):
    report_id: str
    total_gaps: int
    critical_count: int
    gaps: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    generated_at: str


class CurriculumResponse(BaseModel):
    curriculum_id: str
    skill_name: str
    target_level: str
    title: str
    description: str
    learning_objectives: list[str]
    modules: list[dict[str, Any]]
    benchmark_id: str
    created_at: str


class EvolutionCycleResponse(BaseModel):
    cycle_id: str
    status: str
    trigger_reason: str
    duration_seconds: float
    skill_gap_report: dict[str, Any]
    hypotheses: list[dict[str, Any]]
    curricula: list[dict[str, Any]]
    experiments: list[dict[str, Any]]
    persisted_record_ids: list[str]
    timestamp: str


class EvolutionRecordResponse(BaseModel):
    id: str
    record_type: str
    hypothesis: str
    experiment_details: dict[str, Any]
    baseline_metrics: dict[str, Any]
    experiment_metrics: dict[str, Any]
    status: str
    version: str
    created_at: datetime


class ToolDetailResponse(BaseModel):
    id: str
    tool_id: str
    name: str
    version: str
    description: str
    category: str
    code: str
    test_code: str
    interface_schema: dict[str, Any] = {}
    dependencies: list[str] = []
    author_id: str
    status: str
    safety_status: str
    safety_violations: list[str] = []
    verification_receipt: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime


class ToolExecutionResponse(BaseModel):
    status: str
    result: Any = None
    error: Optional[str] = None
    receipt: Optional[dict[str, Any]] = None


class MethodDetailResponse(BaseModel):
    id: str
    method_id: str
    name: str
    version: str
    domain: str
    objective: str
    strategy_type: str
    steps: list[dict[str, Any]] = []
    fallback_strategy: dict[str, Any] = {}
    status: str
    baseline_method_id: Optional[str] = None
    author_id: str
    tags: list[str] = []
    promoted_at: Optional[datetime] = None
    created_at: datetime
    benchmarks_count: int = 0


class MethodBenchmarkResponse(BaseModel):
    id: str
    benchmark_id: str
    method_id: str
    evaluation_status: str = "not_evaluated"
    total_scenarios: int
    passed_scenarios: int
    accuracy: float
    avg_latency_ms: float
    token_efficiency: float
    reproduction_rate: float
    composite_score: float
    baseline_accuracy: Optional[float] = None
    baseline_latency_ms: Optional[float] = None
    baseline_token_efficiency: Optional[float] = None
    relative_improvement_pct: float
    recommendation: str
    scenario_results: list[dict[str, Any]] = []
    recorded_at: datetime


class SelfImprovementCycleResponse(BaseModel):
    cycle_id: str
    status: str
    trigger_reason: str
    duration_seconds: float
    failure_patterns_detected: list[dict[str, Any]] = []
    actions_taken: list[str] = []
    tools_authored: list[dict[str, Any]] = []
    methods_synthesized: list[dict[str, Any]] = []
    prompts_optimized: list[dict[str, Any]] = []
    memories_stored_count: int
    evolution_record_id: Optional[str] = None
    timestamp: str


class MemoryEntryResponse(BaseModel):
    id: str
    category: str
    source: str
    content: str
    metadata_json: dict[str, Any] = {}
    confidence: float
    created_at: datetime






