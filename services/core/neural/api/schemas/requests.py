from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from neural.constants import ActionItemStatus, ExecutionMode, MeetingType, ProposalType, VoteChoice
from pydantic import BaseModel, Field, field_validator, model_validator


class ObjectiveSubmissionRequest(BaseModel):
    objective: str = Field(..., description="High-level project objective from human owner")
    project_name: Optional[str] = Field(None, description="Optional name for the project")
    budget_usd: Optional[float] = Field(
        None,
        gt=0,
        le=1_000_000,
        allow_inf_nan=False,
        description="Requested hard project budget in USD",
    )
    project_budget_usd: Optional[float] = Field(
        None,
        gt=0,
        le=1_000_000,
        allow_inf_nan=False,
        description="Alias for budget_usd: requested hard project budget in USD",
    )
    deadline_days: Optional[int] = Field(None, ge=1, le=3650, description="Target project deadline in days")
    scheduled_at: Optional[datetime] = Field(
        None, description="Optional UTC start time; task execution is held until this time."
    )
    clarifications: Optional[dict[str, str]] = Field(
        None, description="Answers to previously raised clarifying questions"
    )

    @model_validator(mode="before")
    @classmethod
    def sync_budget_fields_before(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            p_budget = data.get("project_budget_usd")
            b_budget = data.get("budget_usd")
            if p_budget is not None and b_budget is None:
                data["budget_usd"] = p_budget
            elif b_budget is not None and p_budget is None:
                data["project_budget_usd"] = b_budget
            elif p_budget is not None and b_budget is not None:
                if p_budget != b_budget:
                    raise ValueError("project_budget_usd and budget_usd must match if both are provided")
        return data

    @model_validator(mode="after")
    def sync_budget_fields_after(self) -> "ObjectiveSubmissionRequest":
        if self.project_budget_usd is not None and self.budget_usd is None:
            self.budget_usd = self.project_budget_usd
        elif self.budget_usd is not None and self.project_budget_usd is None:
            self.project_budget_usd = self.budget_usd
        elif self.project_budget_usd is not None and self.budget_usd is not None:
            if self.project_budget_usd != self.budget_usd:
                raise ValueError("project_budget_usd and budget_usd must match if both are provided")
        return self

    @field_validator("scheduled_at")
    @classmethod
    def normalize_scheduled_at(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone offset")
        normalized = value.astimezone(timezone.utc).replace(tzinfo=None)
        if normalized <= datetime.now(timezone.utc).replace(tzinfo=None):
            raise ValueError("scheduled_at must be in the future")
        return normalized


class TaskRecoveryApprovalRequest(BaseModel):
    justification: str = Field(min_length=8, max_length=2000)


class TaskRecoveryRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=64)


class RuntimeHeartbeatRequest(BaseModel):
    instance_id: str = Field(min_length=16, max_length=64)


class CreateEmployeeRequest(BaseModel):
    name: str
    title: str
    department: str = "Engineering"
    role_id: Optional[str] = None
    personality: Optional[str] = None
    communication_style: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    desk_id: Optional[str] = None
    avatar_config: Optional[dict[str, Any]] = None


class ScheduleMeetingRequest(BaseModel):
    title: str
    meeting_type: MeetingType
    agenda: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)


class ConductMeetingRequest(BaseModel):
    employee_reports: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    action_items: list[dict[str, Any]] = Field(default_factory=list)
    carry_over: bool = True
    summary: Optional[str] = None


class SubmitProgressReportRequest(BaseModel):
    model_config = {"extra": "allow"}
    employee_id: str
    task_id: str
    what_was_done: str
    what_remains: str
    what_is_blocking: Optional[str] = None
    verification_receipt: dict[str, Any] = Field(default_factory=dict)



class CreateActionItemRequest(BaseModel):
    meeting_id: str
    description: str
    assigned_to: str
    deadline: Optional[datetime] = None
    linked_task_id: Optional[str] = None
    acceptance_criteria: Optional[str] = None


class UpdateActionItemRequest(BaseModel):
    status: Optional[ActionItemStatus] = None
    deadline: Optional[datetime] = None
    linked_task_id: Optional[str] = None
    acceptance_criteria: Optional[str] = None


class ReopenActionItemRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="Formal justification for reopening resolved action item")
    reopened_by: Optional[str] = Field("BossAI", description="Identity of authority reopening the item")


class AdvanceAgendaStageRequest(BaseModel):
    stage: str = Field(..., min_length=2, max_length=64, description="Target agenda stage to advance to")


class ValidateClaimRequest(BaseModel):
    task_id: str
    claim: str
    commit_sha: str
    artifact_hash: str
    test_run_id: str
    verification_status: str
    verified_at: str


class AssignDeskRequest(BaseModel):
    desk_id: str = Field(..., min_length=2, max_length=64, description="Canonical workstation desk identifier")


class PromoteEmployeeRequest(BaseModel):
    new_title: str = Field(..., min_length=2, max_length=128, description="Target promoted job title")
    new_model_tier: Optional[str] = Field(None, max_length=64, description="Upgraded model tier (e.g. reasoning, coding)")
    additional_tools: Optional[list[str]] = Field(None, description="Additional tools granted with promotion")
    reason: Optional[str] = Field(None, max_length=512, description="Merit-based promotion rationale")


class SetMeetingSpeakerRequest(BaseModel):
    speaker_id: str = Field(..., min_length=1, max_length=64, description="Participant employee ID or BossAI")
    reason: Optional[str] = Field(None, max_length=256, description="Context or agenda topic for speaker transition")


class ConductOperationalReviewRequest(BaseModel):
    project_id: Optional[str] = Field(default=None, description="Optional associated project ID")
    task_id: str = Field(..., min_length=1)
    reporting_employee_id: str = Field(..., min_length=1)
    what_was_done: str = Field(..., min_length=3)
    what_remains: str = Field(..., min_length=3)
    what_is_blocking: str = Field(..., min_length=3)
    verification_receipt: dict[str, Any] = Field(..., description="Factual verification proof")
    decision_topic: str = Field(..., min_length=3)
    decision_text: str = Field(..., min_length=3)
    decision_rationale: str = Field(..., min_length=3)
    action_item_description: str = Field(..., min_length=3)
    action_item_assigned_to: str = Field(..., min_length=1)
    action_item_deadline_hours: Optional[int] = Field(default=24, ge=1, le=720)
    as_governance: Optional[bool] = Field(default=False, description="Whether to route decision through Governance Engine")
    linked_task_id: Optional[str] = Field(default=None, description="Linked Task DAG identifier")
    acceptance_criteria: Optional[str] = Field(default=None, description="Acceptance criteria for action item")


class RecordDecisionRequest(BaseModel):
    topic: str
    decision: str
    rationale: str
    decided_by: str = "BossAI"
    impact: str = "Standard workflow"
    as_governance: bool = False
    proposal_type: Optional[ProposalType] = None
    project_id: Optional[str] = None
    tasks: Optional[list[dict[str, Any]]] = None


class TriggerBlockerMeetingRequest(BaseModel):
    task_id: str
    blocker_reason: str
    project_id: Optional[str] = None


class TriggerPhaseMeetingRequest(BaseModel):
    project_id: str
    phase_id: str
    completed_tasks: Optional[list[str]] = None


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., description="'approved' or 'rejected'")
    notes: Optional[str] = None


class AssignTaskRequest(BaseModel):
    employee_id: str = Field(..., description="Employee ID to assign task to")


class UpdateTaskStatusRequest(BaseModel):
    status: str = Field(..., description="Target task status")
    notes: Optional[str] = None


class TaskCancelRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000, description="Reason for cancelling the task")
    requeue: bool = Field(False, description="Whether to requeue the task instead of terminating it as cancelled")
    standby_employee_id: Optional[str] = Field(None, description="Optional standby employee to reassign to upon requeue")


class WorkerLeaseRequest(BaseModel):
    fencing_token: int = Field(..., ge=1)


class WorkerClaimRequest(BaseModel):
    execution_modes: list[ExecutionMode] = Field(
        default_factory=lambda: list(ExecutionMode), min_length=1, max_length=10
    )


class WorkerCommandRequest(WorkerLeaseRequest):
    command: list[Annotated[str, Field(min_length=1, max_length=8192)]] = Field(
        ..., min_length=1, max_length=64
    )

    @field_validator("command")
    @classmethod
    def allowlisted_executable(cls, command: list[str]) -> list[str]:
        if command[0] not in {"python", "python3", "pytest", "npm", "npx"}:
            raise ValueError("Command executable is not allowed for terminal task execution.")
        return command


class WorkerSubmitRequest(WorkerLeaseRequest):
    execution_receipt: dict[str, Any] = Field(..., min_length=1)


class WorkerFailureRequest(WorkerLeaseRequest):
    error_message: str = Field(..., min_length=1, max_length=4000)


class WorkerGuiStep(BaseModel):
    action_type: Literal[
        "MOUSE_MOVE",
        "MOUSE_CLICK",
        "MOUSE_DOUBLE_CLICK",
        "MOUSE_RIGHT_CLICK",
        "MOUSE_DRAG",
        "MOUSE_SCROLL",
        "KEY_PRESS",
        "KEY_TYPE",
        "KEY_HOTKEY",
        "WAIT_SETTLE",
    ]
    target: Optional[str] = Field(None, description="Text/descriptor to ground on the live screen")
    text: Optional[str] = Field(None, description="Text to type")
    key_code: Optional[str] = Field(None, description="Key or hotkey specification")
    coordinates: Optional[tuple[int, int]] = Field(None, description="Explicit pixel coordinates")
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    expected_postcondition: Optional[str] = None


class WorkerGuiPlanRequest(WorkerLeaseRequest):
    steps: list[WorkerGuiStep] = Field(..., min_length=1, max_length=200)
    expected_text: Optional[str] = Field(None, description="Text that must be visible after execution")
    expected_postcondition: Optional[str] = None
    display: Optional[str] = Field(None, description="X display to drive; defaults to the worker environment")


class WorkerGuiStepRequest(WorkerLeaseRequest):
    step: WorkerGuiStep
    display: Optional[str] = None


class VerificationDecisionRequest(BaseModel):
    passed: bool
    notes: str = Field(..., min_length=1, max_length=4000)
    evidence_refs: list[Annotated[str, Field(min_length=1)]] = Field(..., min_length=1)
    checks: list[dict[str, Any]] = Field(..., min_length=1)
    test_summary: dict[str, Any] = Field(default_factory=dict)


class VerifierCommandRequest(WorkerLeaseRequest):
    command: list[Annotated[str, Field(min_length=1, max_length=8192)]] = Field(
        ..., min_length=1, max_length=64
    )

    @field_validator("command")
    @classmethod
    def require_test_command(cls, command: list[str]) -> list[str]:
        is_pytest = command[0] == "pytest" or (
            command[0] in {"python", "python3"} and command[1:3] == ["-m", "pytest"]
        )
        is_unittest = (
            command[0] in {"python", "python3"} and command[1:3] == ["-m", "unittest"]
        )
        is_npm_test = command[0] == "npm" and (
            command[1:2] == ["test"] or command[1:3] == ["run", "test"]
        )
        if not (is_pytest or is_unittest or is_npm_test):
            raise ValueError("Verifier execution is restricted to pytest, unittest, or npm test commands.")
        return command


class VerifierManifestRequest(WorkerLeaseRequest):
    """Fence-only request for a read-only verifier workspace snapshot."""


class ResourceLeaseClaimRequest(BaseModel):
    resource_key: str = Field(..., min_length=1, max_length=128)
    employee_id: str = Field(..., min_length=1, max_length=128)
    task_id: Optional[str] = Field(None, max_length=128)
    session_id: Optional[str] = Field(None, max_length=128)
    lease_seconds: int = Field(default=60, ge=5, le=3600)
    resource_reservation: dict[str, Any] = Field(default_factory=dict)


class ResourceLeaseTokenRequest(BaseModel):
    fencing_token: int = Field(..., ge=1)


class ExecuteDagRequest(BaseModel):
    max_concurrency: Optional[int] = Field(5, description="Max parallel workers")


class DirectMessageRequest(BaseModel):
    sender: str
    recipient: str
    content: str
    subject: Optional[str] = None


class DepartmentBroadcastRequest(BaseModel):
    title: str
    content: str
    sender: str = "boss"
    priority: str = "normal"


class BroadcastRequest(BaseModel):
    title: str
    content: str
    sender: str = "boss"
    priority: str = "normal"


class LLMGenerateRequest(BaseModel):
    prompt: str
    tier: Optional[str] = None
    route: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    employee_id: Optional[str] = None
    task_id: Optional[str] = None
    project_id: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 2000


class CreateProposalRequest(BaseModel):
    title: str = Field(..., description="Proposal title")
    description: str = Field(..., description="Detailed proposal narrative and motivation")
    proposal_type: ProposalType = Field(default=ProposalType.STRATEGIC_INITIATIVE)
    proposer_id: str = Field(default="BossAI")
    proposer_role: str = Field(default="Chief Architect / CEO")
    project_id: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    quorum_threshold: float = Field(default=0.5, ge=0.1, le=1.0)
    approval_threshold: float = Field(default=0.5, ge=0.1, le=1.0)
    auto_discuss: bool = True


class CastVoteRequest(BaseModel):
    voter_id: str = Field(..., description="Committee member or Employee ID")
    choice: VoteChoice = Field(..., description="'aye', 'nay', or 'abstain'")
    voter_name: Optional[str] = None
    voter_role: Optional[str] = None
    department: Optional[str] = None
    weight: Optional[float] = None
    rationale: Optional[str] = None


class DeliberateProposalRequest(BaseModel):
    member_ids: Optional[list[str]] = Field(default=None, description="Optional specific committee member IDs to deliberate")


class FinalizeVotingRequest(BaseModel):
    force_decision: Optional[str] = Field(default=None, description="Optional override 'APPROVED' or 'REJECTED'")


class VetoProposalRequest(BaseModel):
    reason: str = Field(..., description="Justification for executive veto")
    vetoed_by: str = Field(default="BossAI", description="Executive actor executing veto")


class CancelProposalRequest(BaseModel):
    reason: str = Field(..., description="Justification for proposal cancellation")
    cancelled_by: str = Field(default="BossAI", description="Actor cancelling the proposal")


class StartCertificationRequest(BaseModel):
    employee_id: str = Field(..., description="Employee identifier, e.g. ANC-EMP-0001")
    skill_name: str = Field(..., description="Target technical skill name, e.g. Python, FastAPI, pytest")
    target_level: str = Field(default="competent", description="Target competency tier: novice, competent, expert, master")
    code_submission: str = Field(..., description="Candidate Python solution code to evaluate in sandbox")
    test_override: Optional[str] = Field(default=None, description="Optional custom test suite code override")
    timeout: Optional[float] = Field(default=5.0, description="Execution timeout limit in seconds")


class TriggerEvolutionCycleRequest(BaseModel):
    trigger_reason: str = Field(default="manual_api_trigger", description="Reason triggering the evolution loop")
    target_skills: Optional[list[str]] = Field(default=None, description="Optional list of skills to focus on")


class GenerateCurriculumRequest(BaseModel):
    skill_name: str = Field(..., description="Skill name to design training curriculum for")
    target_level: str = Field(default="competent", description="Target competency tier")
    gap_reason: Optional[str] = Field(default=None, description="Specific gap or failure context motivating curriculum")


class RunSkillGapAnalysisRequest(BaseModel):
    include_projects: bool = Field(default=True, description="Whether to include pending project tasks")
    include_failures: bool = Field(default=True, description="Whether to include recent task failures")


class AuthorToolRequest(BaseModel):
    name: str = Field(..., description="Tool name, e.g. json_sanitizer")
    code: str = Field(..., description="Python source code of tool")
    test_code: str = Field(..., description="Executable Python test harness")
    description: str = Field(default="", description="Tool purpose and behavior description")
    version: str = Field(default="1.0.0", description="Semver version string")
    category: str = Field(default="utility", description="Tool category (e.g. utility, security, data)")
    interface_schema: Optional[dict[str, Any]] = Field(default=None, description="Optional OpenAPI schema")
    dependencies: Optional[list[str]] = Field(default=None, description="Declared dependencies")
    author_id: str = Field(default="autonomous_toolsmith", description="Author agent/employee ID")
    auto_test: bool = Field(default=True, description="Whether to run sandbox validation tests immediately")
    timeout: float = Field(default=5.0, description="Test execution timeout in seconds")


class TestToolRequest(BaseModel):
    test_code_override: Optional[str] = Field(default=None, description="Optional test suite override")
    timeout: float = Field(default=5.0, description="Execution timeout limit in seconds")


class ExecuteToolRequest(BaseModel):
    tool_name_or_id: str = Field(..., description="Registered tool name or unique tool_id")
    function_name: str = Field(..., description="Exported tool function name to execute")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Function keyword arguments")
    timeout: float = Field(default=5.0, description="Execution timeout limit in seconds")


class SynthesizeMethodRequest(BaseModel):
    name: str = Field(..., description="Problem-solving method name")
    objective: str = Field(..., description="Target outcome or objective")
    strategy_type: str = Field(default="decomposition_parallel", description="Strategy type (e.g. decomposition_parallel, spec_driven_synthesis, chain_of_verification)")
    domain: str = Field(default="engineering", description="Organizational domain")
    steps: Optional[list[dict[str, Any]]] = Field(default=None, description="Explicit step specifications")
    version: str = Field(default="1.0.0", description="Method semver string")
    baseline_method_id: Optional[str] = Field(default=None, description="ID of baseline method to improve upon")
    author_id: str = Field(default="MethodLabAI", description="Author agent or department")
    tags: Optional[list[str]] = Field(default=None, description="Searchable taxonomy tags")


class BenchmarkMethodRequest(BaseModel):
    baseline_method_id: Optional[str] = Field(default=None, description="Optional baseline method ID override")


class PromoteMethodRequest(BaseModel):
    force: bool = Field(default=False, description="Whether to bypass benchmark threshold requirements")


class TriggerSelfImprovementRequest(BaseModel):
    trigger_reason: str = Field(default="manual_api_trigger", description="Trigger context")
    auto_apply: bool = Field(default=False, description="Legacy flag; promotion remains behind human approval")
    sample_failures: Optional[list[dict[str, Any]]] = Field(default=None, description="Optional failure logs to analyze")


class OptimizePromptRequest(BaseModel):
    current_prompt: str = Field(..., description="Base system prompt")
    pattern_type: str = Field(default="ASYNC_SESSION_MISMANAGEMENT", description="Target failure pattern type")
    target_role: str = Field(default="Senior Engineer", description="Target employee role title")


class StoreMemoryRequest(BaseModel):
    category: str = Field(default="lesson", description="Memory category: episode, knowledge, procedure, policy, lesson")
    source: str = Field(default="manual_input", description="Origin source identifier")
    content: str = Field(..., description="Memory content text")
    metadata_json: Optional[dict[str, Any]] = Field(default=None, description="Structured metadata payload")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")


class CreateEvolutionProposalRequest(BaseModel):
    title: str = Field(..., description="Title of the improvement proposal")
    description: str = Field(..., description="Detailed description")
    category: str = Field(default="optimization", description="Proposal category: optimization, bug_fix, performance, reliability")
    target_files: list[str] = Field(..., description="Target file paths to modify")
    root_cause_rationale: str = Field(..., description="Root cause rationale and empirical weakness evidence")
    baseline_metrics: dict[str, Any] = Field(..., description="Empirical baseline performance metrics")
    target_goals: dict[str, Any] = Field(..., description="Measurable target goals (latency, speedup, error rate)")
    proposer_id: str = Field(default="SelfEvolutionEngine", description="Proposer identity")


class StageEvolutionCandidateRequest(BaseModel):
    files: dict[str, str] = Field(..., description="File paths and modified source code contents to apply")
    commit_message: str = Field(..., description="Git commit message")
    author_id: str = Field(default="SelfEvolutionEngine", description="Author identity")
    base_ref: str = Field(default="HEAD", description="Base commit or branch ref")


class VerifyEvolutionCandidateRequest(BaseModel):
    test_command: Optional[list[str]] = Field(default=None, description="Optional custom test execution command")
    candidate_benchmark_metrics: Optional[dict[str, Any]] = Field(default=None, description="Empirical candidate benchmark measurements")


class PromoteEvolutionCandidateRequest(BaseModel):
    target_branch: str = Field(default="main", description="Target production branch")
    approval_id: Optional[str] = Field(default=None, description="Cryptographically signed human approval token")
    auto_promote: bool = Field(default=False, description="Legacy auto promote flag; remains strictly approval_required")
    requested_by: str = Field(default="SelfEvolutionEngine", description="Requesting agent/employee identity")


class RollbackEvolutionCandidateRequest(BaseModel):
    target_branch: str = Field(default="main", description="Target branch to restore")
    pre_evolution_commit: str = Field(..., description="Pinned pre-evolution commit hash to restore")
    anomaly_detected: dict[str, Any] = Field(..., description="Anomaly or regression details triggering rollback")
    requested_by: str = Field(default="PostPromotionHealthMonitor", description="Actor triggering rollback")


class BossChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Message to Boss AI")
    conversation_history: list[dict[str, Any]] = Field(
        default_factory=list, description="Recent conversation history"
    )
    project_id: Optional[str] = Field(
        None, description="Optional target project ID context"
    )








