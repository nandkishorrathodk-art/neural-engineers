from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_approval_repo,
    get_audit_repo,
    get_employee_repo,
    get_evolution_repo,
    get_project_repo,
    get_task_repo,
)
from neural.api.schemas.requests import (
    CreateEvolutionProposalRequest,
    GenerateCurriculumRequest,
    PromoteEvolutionCandidateRequest,
    RollbackEvolutionCandidateRequest,
    RunSkillGapAnalysisRequest,
    StageEvolutionCandidateRequest,
    TriggerEvolutionCycleRequest,
    VerifyEvolutionCandidateRequest,
)
from neural.api.schemas.responses import (
    CurriculumResponse,
    EvolutionCandidateStagingResponse,
    EvolutionCycleResponse,
    EvolutionPromotionResponse,
    EvolutionProposalResponse,
    EvolutionRecordResponse,
    EvolutionRollbackResponse,
    EvolutionVerificationResponse,
    SkillGapReportResponse,
)
from neural.db.models import EvolutionRecord
from neural.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    EmployeeRepository,
    EvolutionRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.evolution.candidate_staging import (
    CandidateStagingError,
    SelfEvolutionCandidateManager,
)
from neural.evolution.curriculum import CurriculumGenerator
from neural.evolution.gap_analysis import SkillGapAnalyzer
from neural.evolution.lifecycle import SelfEvolutionEngine
from neural.evolution.models import (
    ProtectedPathViolationError,
    SelfEvolutionProposal,
    SelfEvolutionVerificationReceipt,
    is_protected_path,
)
from neural.evolution.multi_gate_verifier import SelfEvolutionMultiGateVerifier
from neural.evolution.promotion_manager import SelfEvolutionPromotionManager
from neural.evolution.proposal_engine import EvidenceBasedProposalEngine
from neural.evolution.rollback_manager import SelfEvolutionRollbackManager
from neural.exceptions import HumanApprovalRequiredError

router = APIRouter(prefix="/evolution", tags=["Recursive Evolution & Continuous Learning"])

# In-memory staging state cache for active proposals
_ACTIVE_PROPOSALS: dict[str, SelfEvolutionProposal] = {}
_ACTIVE_VERIFICATIONS: dict[str, SelfEvolutionVerificationReceipt] = {}


def _get_evolution_paths() -> tuple[Path, Path]:
    repo_path = Path.cwd().resolve()
    managed_worktrees = repo_path.parent / "data" / "evolution_worktrees"
    return repo_path, managed_worktrees


def serialize_evolution_record(r: EvolutionRecord) -> EvolutionRecordResponse:
    return EvolutionRecordResponse(
        id=r.id,
        record_type=r.record_type,
        hypothesis=r.hypothesis,
        experiment_details=r.experiment_details or {},
        baseline_metrics=r.baseline_metrics or {},
        experiment_metrics=r.experiment_metrics or {},
        status=r.status,
        version=r.version,
        created_at=r.created_at,
    )


@router.get("/status")
async def get_evolution_status(
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """Retrieves operational status and summary metrics of the evolution engine."""
    records = await evolution_repo.list_all(limit=100)
    return {
        "status": "active",
        "total_evolution_records": len(records),
        "latest_version": records[0].version if records else "1.0.0",
        "supported_loop_stages": [
            "OBSERVE",
            "IDENTIFY_LIMITATIONS",
            "HYPOTHESIZE",
            "DESIGN_CURRICULUM",
            "EXPERIMENT",
            "BENCHMARK",
            "STAGE_RELEASE",
        ],
    }


@router.post("/gap-analysis", response_model=SkillGapReportResponse)
async def run_skill_gap_analysis(
    req: RunSkillGapAnalysisRequest = RunSkillGapAnalysisRequest(),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    project_repo: ProjectRepository = Depends(get_project_repo),
):
    """
    Executes autonomous skill gap analysis against live tasks and projects.
    Detects capability deficits from failures and unverified workforce claims.
    """
    analyzer = SkillGapAnalyzer(
        employee_repo=employee_repo,
        task_repo=task_repo if req.include_failures else None,
        project_repo=project_repo if req.include_projects else None,
    )
    report = await analyzer.analyze_gaps()
    return SkillGapReportResponse(**report.to_dict())


@router.post("/curriculum/generate", response_model=CurriculumResponse)
async def generate_training_curriculum(
    req: GenerateCurriculumRequest,
):
    """
    Generates a targeted training curriculum with hands-on coding modules,
    test harnesses, and empirical benchmark evaluations.
    """
    try:
        generator = CurriculumGenerator()
        curriculum = generator.generate_curriculum(
            skill_name=req.skill_name,
            target_level=req.target_level,
            gap_reason=req.gap_reason,
        )
        return CurriculumResponse(**curriculum.to_dict())
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))


@router.post("/cycle", response_model=EvolutionCycleResponse)
async def trigger_evolution_cycle(
    req: TriggerEvolutionCycleRequest = TriggerEvolutionCycleRequest(),
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    project_repo: ProjectRepository = Depends(get_project_repo),
):
    """
    Triggers complete autonomous recursive self-evolution cycle:
    OBSERVE -> IDENTIFY -> HYPOTHESIZE -> CURRICULUM -> EXPERIMENT -> RECORD.
    """
    engine = SelfEvolutionEngine(
        evolution_repo=evolution_repo,
        employee_repo=employee_repo,
        task_repo=task_repo,
        project_repo=project_repo,
    )
    cycle_res = await engine.run_evolution_cycle(
        trigger_reason=req.trigger_reason,
        target_skills=req.target_skills,
    )
    return EvolutionCycleResponse(**cycle_res)


# =========================================================================
# NEURO-9: Self-Evolution Proposals, Candidate Staging, Verification, Promotion, Rollback
# =========================================================================

@router.post("/proposals", response_model=EvolutionProposalResponse)
async def create_improvement_proposal(
    req: CreateEvolutionProposalRequest,
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
):
    """
    Creates a structured evidence-based improvement proposal.
    Strictly enforces protected core files invariant: fails closed if protected files are targeted.
    """
    engine = EvidenceBasedProposalEngine(task_repo=task_repo)
    try:
        proposal = engine.create_proposal(
            title=req.title,
            description=req.description,
            category=req.category,
            target_files=req.target_files,
            root_cause_rationale=req.root_cause_rationale,
            baseline_metrics=req.baseline_metrics,
            target_goals=req.target_goals,
            proposer_id=req.proposer_id,
        )
    except ProtectedPathViolationError as err:
        raise HTTPException(status_code=403, detail=str(err))

    # Persist in DB and active cache
    _ACTIVE_PROPOSALS[proposal.proposal_id] = proposal
    await evolution_repo.create_record(
        record_type="self_evolution_proposal",
        hypothesis=f"Self-evolution proposal: {proposal.title}",
        experiment_details={"proposal": proposal.to_dict()},
        baseline_metrics=proposal.baseline_metrics,
        experiment_metrics={"target_goals": proposal.target_goals},
        status=proposal.status.value,
        record_id=proposal.proposal_id,
    )

    return EvolutionProposalResponse(**proposal.to_dict())


@router.get("/proposals", response_model=list[EvolutionProposalResponse])
async def list_improvement_proposals(
    status: Optional[str] = Query(None, description="Filter by proposal status"),
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """Lists all self-evolution proposals."""
    records = await evolution_repo.list_all(record_type="self_evolution_proposal", status=status)
    proposals = []
    for r in records:
        details = r.experiment_details or {}
        p_dict = details.get("proposal")
        if p_dict:
            proposals.append(EvolutionProposalResponse(**p_dict))
    return proposals


@router.get("/proposals/{proposal_id}", response_model=EvolutionProposalResponse)
async def get_improvement_proposal(
    proposal_id: str,
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """Retrieves an improvement proposal by ID."""
    if proposal_id in _ACTIVE_PROPOSALS:
        return EvolutionProposalResponse(**_ACTIVE_PROPOSALS[proposal_id].to_dict())

    record = await evolution_repo.get_by_id(proposal_id)
    if not record or record.record_type != "self_evolution_proposal":
        raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")
    p_dict = record.experiment_details.get("proposal", {})
    return EvolutionProposalResponse(**p_dict)


@router.post("/proposals/{proposal_id}/stage", response_model=EvolutionCandidateStagingResponse)
async def stage_candidate_branch(
    proposal_id: str,
    req: StageEvolutionCandidateRequest,
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """
    Stages code changes into an isolated candidate branch self_evolution/{proposal_id}.
    Fails closed immediately if any modified file is a protected path.
    """
    proposal = _ACTIVE_PROPOSALS.get(proposal_id)
    if not proposal:
        record = await evolution_repo.get_by_id(proposal_id)
        if not record or record.record_type != "self_evolution_proposal":
            raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")
        proposal = SelfEvolutionProposal.from_dict(record.experiment_details["proposal"])
        _ACTIVE_PROPOSALS[proposal_id] = proposal

    repo_path, managed_worktrees = _get_evolution_paths()
    manager = SelfEvolutionCandidateManager(repo_path, managed_worktrees)

    # 1. Pre-check protected core paths invariant before provisioning
    for rel_path in req.files:
        if is_protected_path(rel_path):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Modification attempt to protected path '{rel_path}' is strictly prohibited. "
                    f"Self-evolution candidate changes cannot touch root policy engines, "
                    f"human approval gates, or emergency halt latches."
                ),
            )

    try:
        try:
            receipt = manager.verify_candidate_receipt(proposal_id)
        except CandidateStagingError:
            receipt = manager.provision_candidate_staging(proposal_id, base_ref=req.base_ref)

        head_commit = manager.apply_candidate_changes(
            proposal_id=proposal_id,
            files=req.files,
            commit_message=req.commit_message,
            author_id=req.author_id,
        )
    except ProtectedPathViolationError as err:
        manager.cleanup_candidate_worktree(proposal_id)
        raise HTTPException(status_code=403, detail=str(err))
    except CandidateStagingError as err:
        raise HTTPException(status_code=400, detail=str(err))

    return EvolutionCandidateStagingResponse(
        proposal_id=receipt.proposal_id,
        branch=receipt.branch,
        path=str(receipt.path),
        base_commit=receipt.base_commit,
        receipt_sha256=receipt.receipt_sha256,
        head_commit=head_commit,
    )


@router.post("/proposals/{proposal_id}/verify", response_model=EvolutionVerificationResponse)
async def verify_candidate(
    proposal_id: str,
    req: VerifyEvolutionCandidateRequest = VerifyEvolutionCandidateRequest(),
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """
    Executes independent multi-gate verification on the candidate branch.
    Evaluates: 1) Full regression testing, 2) Static analysis, 3) AST security, 4) Benchmark comparison.
    Signed by IndependentDeliverableVerifier.
    """
    proposal = _ACTIVE_PROPOSALS.get(proposal_id)
    if not proposal:
        record = await evolution_repo.get_by_id(proposal_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")
        proposal = SelfEvolutionProposal.from_dict(record.experiment_details["proposal"])
        _ACTIVE_PROPOSALS[proposal_id] = proposal

    repo_path, managed_worktrees = _get_evolution_paths()
    candidate_mgr = SelfEvolutionCandidateManager(repo_path, managed_worktrees)
    verifier = SelfEvolutionMultiGateVerifier(candidate_mgr)

    try:
        receipt = verifier.verify_candidate(
            proposal=proposal,
            test_command=req.test_command,
            candidate_benchmark_metrics=req.candidate_benchmark_metrics,
        )
    except ProtectedPathViolationError as err:
        raise HTTPException(status_code=403, detail=str(err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Multi-gate verification error: {exc}")

    _ACTIVE_VERIFICATIONS[proposal_id] = receipt

    # Record verification outcome
    await evolution_repo.create_record(
        record_type="self_evolution_verification",
        hypothesis=f"Verification for proposal {proposal_id}",
        experiment_details=receipt.to_dict(),
        baseline_metrics=proposal.baseline_metrics,
        experiment_metrics=receipt.benchmark_comparison,
        status="verified" if receipt.passed else "failed",
        record_id=receipt.receipt_id,
    )

    return EvolutionVerificationResponse(**receipt.to_dict())


@router.post("/proposals/{proposal_id}/promote", response_model=EvolutionPromotionResponse)
async def promote_candidate(
    proposal_id: str,
    req: PromoteEvolutionCandidateRequest,
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
):
    """
    Executes approval-gated staged promotion into production.
    INVARIANT: Even if auto_promote=True is requested, automatic promotion is strictly blocked.
    Promotion into production strictly requires an approved human approval token from HumanApprovalGate.
    """
    proposal = _ACTIVE_PROPOSALS.get(proposal_id)
    if not proposal:
        record = await evolution_repo.get_by_id(proposal_id)
        if not record:
            raise HTTPException(status_code=404, detail=f"Proposal '{proposal_id}' not found")
        proposal = SelfEvolutionProposal.from_dict(record.experiment_details["proposal"])
        _ACTIVE_PROPOSALS[proposal_id] = proposal

    verif_receipt = _ACTIVE_VERIFICATIONS.get(proposal_id)
    if not verif_receipt:
        raise HTTPException(
            status_code=400,
            detail=f"Candidate proposal '{proposal_id}' has not passed multi-gate verification yet.",
        )

    repo_path, managed_worktrees = _get_evolution_paths()
    candidate_mgr = SelfEvolutionCandidateManager(repo_path, managed_worktrees)
    promotion_mgr = SelfEvolutionPromotionManager(candidate_mgr)

    # 1. Evaluate promotion eligibility
    readiness = promotion_mgr.assess_promotion_readiness(
        proposal=proposal,
        verification_receipt=verif_receipt,
        auto_promote=req.auto_promote,
    )

    # If no approval token supplied, return approval_required response
    if not req.approval_id:
        return EvolutionPromotionResponse(
            proposal_id=proposal_id,
            target_branch=req.target_branch,
            activation_status="approval_required",
            eligible_for_promotion=readiness["eligible_for_promotion"],
            auto_promote_blocked=readiness.get("auto_promote_blocked", False),
            message=readiness["message"],
        )

    # 2. Execute promotion with approved token
    try:
        prom_receipt = await promotion_mgr.execute_promotion(
            proposal_id=proposal_id,
            target_branch=req.target_branch,
            verification_receipt=verif_receipt,
            approval_id=req.approval_id,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
            requested_by=req.requested_by,
        )
    except HumanApprovalRequiredError as err:
        raise HTTPException(status_code=403, detail=str(err))
    except ProtectedPathViolationError as err:
        raise HTTPException(status_code=403, detail=str(err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Promotion failed: {exc}")

    # Record promotion
    await evolution_repo.create_record(
        record_type="self_evolution_promotion",
        hypothesis=f"Promotion for proposal {proposal_id}",
        experiment_details=prom_receipt.to_dict(),
        status="promoted",
        record_id=prom_receipt.receipt_id,
    )

    return EvolutionPromotionResponse(
        receipt_id=prom_receipt.receipt_id,
        proposal_id=proposal_id,
        candidate_branch=prom_receipt.candidate_branch,
        target_branch=prom_receipt.target_branch,
        pre_evolution_commit=prom_receipt.pre_evolution_commit,
        promoted_commit=prom_receipt.promoted_commit,
        approval_id=prom_receipt.approval_id,
        approved_by=prom_receipt.approved_by,
        verification_receipt_id=prom_receipt.verification_receipt_id,
        activation_status="promoted",
        eligible_for_promotion=True,
        auto_promote_blocked=False,
        timestamp=prom_receipt.timestamp,
        signature=prom_receipt.signature,
        message="Self-evolution candidate successfully promoted into production.",
    )


@router.post("/proposals/{proposal_id}/rollback", response_model=EvolutionRollbackResponse)
async def rollback_candidate(
    proposal_id: str,
    req: RollbackEvolutionCandidateRequest,
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo),
):
    """
    Executes automated fail-closed rollback to the pinned pre-evolution commit state.
    """
    repo_path, _ = _get_evolution_paths()
    rollback_mgr = SelfEvolutionRollbackManager(repo_path)

    try:
        rb_receipt = await rollback_mgr.execute_rollback(
            proposal_id=proposal_id,
            target_branch=req.target_branch,
            pre_evolution_commit=req.pre_evolution_commit,
            anomaly_detected=req.anomaly_detected,
            audit_repo=audit_repo,
            requested_by=req.requested_by,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Rollback failed: {exc}")

    # Record rollback
    await evolution_repo.create_record(
        record_type="self_evolution_rollback",
        hypothesis=f"Rollback for proposal {proposal_id}",
        experiment_details=rb_receipt.to_dict(),
        status="rolled_back",
        record_id=rb_receipt.receipt_id,
    )

    return EvolutionRollbackResponse(**rb_receipt.to_dict())


# =========================================================================
# Historical Evolution Records
# =========================================================================

@router.get("/records", response_model=list[EvolutionRecordResponse])
async def list_evolution_records(
    record_type: str | None = Query(None, description="Filter by type (curriculum, prompt, tool, model, self_evolution_*)"),
    status: str | None = Query(None, description="Filter by status (completed, in_review, verified, promoted, rolled_back)"),
    limit: int = Query(50, ge=1, le=100),
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """Lists historical evolution and experiment records."""
    records = await evolution_repo.list_all(
        record_type=record_type,
        status=status,
        limit=limit,
    )
    return [serialize_evolution_record(r) for r in records]


@router.get("/records/{record_id}", response_model=EvolutionRecordResponse)
async def get_evolution_record(
    record_id: str,
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
):
    """Retrieves specific evolution experiment record by ID."""
    record = await evolution_repo.get_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Evolution record '{record_id}' not found")
    return serialize_evolution_record(record)
