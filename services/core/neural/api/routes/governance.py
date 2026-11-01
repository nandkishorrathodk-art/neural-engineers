from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_governance_engine,
    get_ledger_repo,
    get_proposal_repo,
    get_vote_repo,
)
from neural.api.schemas.requests import (
    CancelProposalRequest,
    CastVoteRequest,
    CreateProposalRequest,
    DeliberateProposalRequest,
    FinalizeVotingRequest,
    VetoProposalRequest,
)
from neural.api.schemas.responses import (
    CommitteeMemberResponse,
    DecisionLedgerEntryResponse,
    LedgerVerificationResponse,
    ProposalDetailResponse,
    ProposalResponse,
    VoteResponse,
)
from neural.constants import ProposalStatus
from neural.db.models import DecisionLedgerEntry, Proposal, Vote
from neural.db.repositories import (
    DecisionLedgerRepository,
    ProposalRepository,
    VoteRepository,
)
from neural.governance.engine import BoardroomGovernanceEngine

router = APIRouter(prefix="/governance", tags=["Boardroom & Strategic Governance"])


def serialize_vote(v: Vote) -> VoteResponse:
    return VoteResponse(
        id=v.id,
        proposal_id=v.proposal_id,
        voter_id=v.voter_id,
        voter_name=v.voter_name,
        voter_role=v.voter_role,
        department=v.department,
        choice=v.choice,
        weight=v.weight,
        rationale=v.rationale,
        voted_at=v.voted_at,
    )


def serialize_proposal(p: Proposal) -> ProposalResponse:
    return ProposalResponse(
        id=p.id,
        proposal_id=p.proposal_id,
        title=p.title,
        description=p.description,
        proposal_type=p.proposal_type,
        status=p.status,
        proposer_id=p.proposer_id,
        proposer_role=p.proposer_role,
        project_id=p.project_id,
        quorum_threshold=p.quorum_threshold,
        approval_threshold=p.approval_threshold,
        created_at=p.created_at,
        updated_at=p.updated_at,
        resolved_at=p.resolved_at,
        executed_at=p.executed_at,
        votes_count=len(p.__dict__["votes"]) if p.__dict__.get("votes") else 0,
    )


def serialize_proposal_detail(
    p: Proposal,
    ledger_entry: DecisionLedgerEntry | None = None,
    votes: list[Vote] | None = None
) -> ProposalDetailResponse:
    target_ledger = ledger_entry
    if target_ledger is None:
        target_ledger = getattr(p, "ledger_entry", None)

    ledger_data = None
    if target_ledger:
        ledger_data = {
            "id": target_ledger.id,
            "entry_id": target_ledger.entry_id,
            "proposal_id": target_ledger.proposal_id,
            "sequence_number": target_ledger.sequence_number,
            "decision": target_ledger.decision,
            "title": target_ledger.title,
            "proposal_type": target_ledger.proposal_type,
            "proposer_id": target_ledger.proposer_id,
            "votes_summary": target_ledger.votes_summary,
            "rationale": target_ledger.rationale,
            "execution_receipt": target_ledger.execution_receipt,
            "previous_hash": target_ledger.previous_hash,
            "entry_hash": target_ledger.entry_hash,
            "recorded_at": target_ledger.recorded_at.isoformat() if hasattr(target_ledger.recorded_at, "isoformat") else str(target_ledger.recorded_at),
        }

    target_votes = votes
    if target_votes is None:
        target_votes = p.__dict__.get("votes") or []

    return ProposalDetailResponse(
        id=p.id,
        proposal_id=p.proposal_id,
        title=p.title,
        description=p.description,
        proposal_type=p.proposal_type,
        status=p.status,
        proposer_id=p.proposer_id,
        proposer_role=p.proposer_role,
        project_id=p.project_id,
        parameters=p.parameters or {},
        deliberation_history=p.deliberation_history or [],
        quorum_threshold=p.quorum_threshold,
        approval_threshold=p.approval_threshold,
        created_at=p.created_at,
        updated_at=p.updated_at,
        resolved_at=p.resolved_at,
        executed_at=p.executed_at,
        execution_receipt=p.execution_receipt,
        votes=[serialize_vote(v) for v in target_votes],
        ledger_entry=ledger_data,
    )




def serialize_ledger_entry(e: DecisionLedgerEntry) -> DecisionLedgerEntryResponse:
    return DecisionLedgerEntryResponse(
        id=e.id,
        entry_id=e.entry_id,
        proposal_id=e.proposal_id,
        sequence_number=e.sequence_number,
        decision=e.decision,
        title=e.title,
        proposal_type=e.proposal_type,
        proposer_id=e.proposer_id,
        votes_summary=e.votes_summary,
        rationale=e.rationale,
        execution_receipt=e.execution_receipt,
        previous_hash=e.previous_hash,
        entry_hash=e.entry_hash,
        recorded_at=e.recorded_at,
    )


@router.post("/proposals", response_model=ProposalResponse)
async def create_proposal(
    payload: CreateProposalRequest,
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Creates a new strategic proposal for boardroom deliberation.
    """
    proposal = await engine.create_proposal(
        title=payload.title,
        description=payload.description,
        proposal_type=payload.proposal_type,
        proposer_id=payload.proposer_id,
        proposer_role=payload.proposer_role,
        project_id=payload.project_id,
        parameters=payload.parameters,
        quorum_threshold=payload.quorum_threshold,
        approval_threshold=payload.approval_threshold,
        auto_discuss=payload.auto_discuss,
    )
    return serialize_proposal(proposal)


@router.get("/proposals", response_model=list[ProposalResponse])
async def list_proposals(
    status: ProposalStatus | None = None,
    project_id: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    proposal_repo: ProposalRepository = Depends(get_proposal_repo),
):
    """
    Lists corporate proposals with optional filtering by status or project.
    """
    proposals = await proposal_repo.list_all(
        status=status, project_id=project_id, limit=limit
    )
    return [serialize_proposal(p) for p in proposals]


@router.get("/proposals/{proposal_id}", response_model=ProposalDetailResponse)
async def get_proposal(
    proposal_id: str,
    proposal_repo: ProposalRepository = Depends(get_proposal_repo),
    ledger_repo: DecisionLedgerRepository = Depends(get_ledger_repo),
    vote_repo: VoteRepository = Depends(get_vote_repo),
):
    """
    Retrieves full details of a proposal including deliberation debate and votes.
    """
    proposal = await proposal_repo.get_by_proposal_id(proposal_id)
    if not proposal:
        raise HTTPException(
            status_code=404, detail=f"Proposal '{proposal_id}' not found"
        )
    votes = await vote_repo.list_by_proposal(proposal.id)
    ledger_entry = await ledger_repo.get_by_proposal_id(proposal.id)
    return serialize_proposal_detail(proposal, ledger_entry=ledger_entry, votes=votes)


@router.post("/proposals/{proposal_id}/deliberate", response_model=ProposalDetailResponse)
async def deliberate_proposal(
    proposal_id: str,
    payload: DeliberateProposalRequest | None = None,
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Triggers structured consensus deliberation across Department Heads / Committee.
    """
    members = None
    if payload and payload.member_ids:
        all_members = engine.committee.get_members(active_only=True)
        members = [m for m in all_members if m.member_id in payload.member_ids]

    try:
        proposal, _ = await engine.run_deliberation(
            proposal_id=proposal_id, members=members
        )
        votes = await engine.vote_repo.list_by_proposal(proposal.id)
        ledger_entry = await engine.ledger_repo.get_by_proposal_id(proposal.id)
        return serialize_proposal_detail(proposal, ledger_entry=ledger_entry, votes=votes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/proposals/{proposal_id}/open-voting", response_model=ProposalResponse)
async def open_voting(
    proposal_id: str,
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Transitions proposal from deliberation to formal voting.
    """
    try:
        proposal = await engine.open_voting(proposal_id)
        return serialize_proposal(proposal)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/proposals/{proposal_id}/vote", response_model=VoteResponse)
async def cast_vote(
    proposal_id: str,
    payload: CastVoteRequest,
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Records a vote from an authorized committee member.
    """
    try:
        vote = await engine.cast_vote(
            proposal_id=proposal_id,
            voter_id=payload.voter_id,
            choice=payload.choice,
            voter_name=payload.voter_name,
            voter_role=payload.voter_role,
            department=payload.department,
            weight=payload.weight,
            rationale=payload.rationale,
        )
        return serialize_vote(vote)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/proposals/{proposal_id}/finalize", response_model=ProposalDetailResponse)
async def finalize_voting(
    proposal_id: str,
    payload: FinalizeVotingRequest | None = None,
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Tallies votes, verifies quorum, determines outcome, and appends to Decision Ledger.
    """
    force = payload.force_decision if payload else None
    try:
        proposal, ledger_entry = await engine.finalize_voting(
            proposal_id=proposal_id, force_decision=force
        )
        votes = await engine.vote_repo.list_by_proposal(proposal.id)
        return serialize_proposal_detail(proposal, ledger_entry=ledger_entry, votes=votes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/proposals/{proposal_id}/veto", response_model=ProposalDetailResponse)
async def veto_proposal(
    proposal_id: str,
    payload: VetoProposalRequest,
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Boss AI Executive Veto. Terminates proposal and records permanent veto in Decision Ledger.
    """
    try:
        proposal, ledger_entry = await engine.veto_proposal(
            proposal_id=proposal_id,
            reason=payload.reason,
            vetoed_by=payload.vetoed_by,
        )
        votes = await engine.vote_repo.list_by_proposal(proposal.id)
        return serialize_proposal_detail(proposal, ledger_entry=ledger_entry, votes=votes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/proposals/{proposal_id}/cancel", response_model=ProposalDetailResponse)
async def cancel_proposal(
    proposal_id: str,
    payload: CancelProposalRequest,
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Cancels an active proposal and records cancellation in Decision Ledger.
    """
    try:
        proposal, ledger_entry = await engine.cancel_proposal(
            proposal_id=proposal_id,
            reason=payload.reason,
            cancelled_by=payload.cancelled_by,
        )
        votes = await engine.vote_repo.list_by_proposal(proposal.id)
        return serialize_proposal_detail(proposal, ledger_entry=ledger_entry, votes=votes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/proposals/{proposal_id}/execute")
async def execute_proposal(
    proposal_id: str,
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Executes an APPROVED proposal: spawns DAG tasks in TaskRepository and sets receipt.
    """
    try:
        proposal, tasks = await engine.execute_proposal(proposal_id)
        votes = await engine.vote_repo.list_by_proposal(proposal.id)
        ledger_entry = await engine.ledger_repo.get_by_proposal_id(proposal.id)
        return {
            "status": "executed",
            "proposal": serialize_proposal_detail(proposal, ledger_entry=ledger_entry, votes=votes),
            "tasks": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "status": t.status.value,
                    "priority": t.priority.value,
                }
                for t in tasks
            ],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))



@router.get("/ledger", response_model=list[DecisionLedgerEntryResponse])
async def list_decision_ledger(
    limit: int = Query(50, ge=1, le=100),
    ledger_repo: DecisionLedgerRepository = Depends(get_ledger_repo),
):
    """
    Lists the immutable, hash-chained Decision Ledger entries in chronological sequence.
    """
    entries = await ledger_repo.list_all(limit=limit)
    return [serialize_ledger_entry(e) for e in entries]


@router.get("/ledger/verify", response_model=LedgerVerificationResponse)
async def verify_ledger(
    ledger_repo: DecisionLedgerRepository = Depends(get_ledger_repo),
):
    """
    Verifies cryptographic hash-chain integrity of the Decision Ledger from genesis to head.
    """
    entries = await ledger_repo.list_all(limit=1000)
    is_valid, error = await ledger_repo.verify_chain_integrity()
    latest_seq = max([e.sequence_number for e in entries], default=0)

    return LedgerVerificationResponse(
        is_valid=is_valid,
        total_entries=len(entries),
        latest_sequence=latest_seq,
        error=error,
        verified_at=datetime.now(UTC).replace(tzinfo=None),
    )


@router.get("/committee", response_model=list[CommitteeMemberResponse])
async def get_committee(
    engine: BoardroomGovernanceEngine = Depends(get_governance_engine),
):
    """
    Lists active Boardroom Committee members and Department Heads with voting weights.
    """
    members = engine.committee.get_members(active_only=True)
    return [
        CommitteeMemberResponse(
            member_id=m.member_id,
            name=m.name,
            role=m.role.value,
            department=m.department,
            voting_weight=m.voting_weight,
            is_active=m.is_active,
        )
        for m in members
    ]
