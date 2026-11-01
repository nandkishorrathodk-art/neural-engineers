from datetime import UTC, datetime

import pytest
from neural.constants import (
    CommitteeRole,
    ProposalStatus,
    ProposalType,
    VoteChoice,
)
from neural.db.models import Vote
from neural.db.repositories import (
    AuditRepository,
    DecisionLedgerRepository,
    ProjectRepository,
    ProposalRepository,
    TaskRepository,
    VoteRepository,
)
from neural.governance.committee import BoardroomCommittee, CommitteeMember
from neural.governance.deliberation import DeliberationEngine
from neural.governance.engine import BoardroomGovernanceEngine
from neural.governance.proposals import ProposalStateMachine
from sqlalchemy.ext.asyncio import AsyncSession


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def test_proposal_state_machine_valid_transitions():
    sm = ProposalStateMachine

    # Happy path: DRAFT -> DISCUSSION -> VOTING -> APPROVED -> EXECUTED
    assert sm.transition(ProposalStatus.DRAFT, ProposalStatus.DISCUSSION) == ProposalStatus.DISCUSSION
    assert sm.transition(ProposalStatus.DISCUSSION, ProposalStatus.VOTING) == ProposalStatus.VOTING
    assert sm.transition(ProposalStatus.VOTING, ProposalStatus.APPROVED) == ProposalStatus.APPROVED
    assert sm.transition(ProposalStatus.APPROVED, ProposalStatus.EXECUTED) == ProposalStatus.EXECUTED

    # Rejection path: VOTING -> REJECTED
    assert sm.transition(ProposalStatus.VOTING, ProposalStatus.REJECTED) == ProposalStatus.REJECTED

    # Veto path: from DISCUSSION or VOTING
    assert sm.transition(ProposalStatus.DISCUSSION, ProposalStatus.VETOED) == ProposalStatus.VETOED
    assert sm.transition(ProposalStatus.VOTING, ProposalStatus.VETOED) == ProposalStatus.VETOED

    # Cancellation path
    assert sm.transition(ProposalStatus.DRAFT, ProposalStatus.CANCELLED) == ProposalStatus.CANCELLED
    assert sm.transition(ProposalStatus.DISCUSSION, ProposalStatus.CANCELLED) == ProposalStatus.CANCELLED

    # Idempotent self-transitions
    assert sm.transition(ProposalStatus.DISCUSSION, ProposalStatus.DISCUSSION) == ProposalStatus.DISCUSSION
    assert sm.transition(ProposalStatus.VOTING, ProposalStatus.VOTING) == ProposalStatus.VOTING


def test_proposal_state_machine_illegal_transitions_rejected():
    sm = ProposalStateMachine

    # Cannot jump DRAFT directly to APPROVED or EXECUTED
    with pytest.raises(ValueError) as exc:
        sm.transition(ProposalStatus.DRAFT, ProposalStatus.APPROVED)
    assert "Illegal proposal state transition" in str(exc.value)

    with pytest.raises(ValueError):
        sm.transition(ProposalStatus.DRAFT, ProposalStatus.EXECUTED)

    # Terminal states cannot transition to anything
    for term in sm.TERMINAL_STATES:
        with pytest.raises(ValueError):
            sm.transition(term, ProposalStatus.DISCUSSION)
        with pytest.raises(ValueError):
            sm.transition(term, ProposalStatus.VOTING)


def test_boardroom_committee_membership_and_quorum():
    committee = BoardroomCommittee()
    assert committee.total_members == 7
    chair = committee.get_member("BossAI")
    assert chair is not None
    assert chair.role == CommitteeRole.CHAIR

    # Quorum check: 7 members, threshold 0.5 (50%)
    # 3 votes / 7 = 42.86% -> below 50%
    is_met, pct = committee.calculate_quorum(votes_cast=3, quorum_threshold=0.5)
    assert is_met is False
    assert pct == 42.86

    # 4 votes / 7 = 57.14% -> meets 50%
    is_met, pct = committee.calculate_quorum(votes_cast=4, quorum_threshold=0.5)
    assert is_met is True
    assert pct == 57.14


def test_boardroom_committee_tally_votes_quorum_failure():
    committee = BoardroomCommittee(
        members=[
            CommitteeMember("M1", "Alice", CommitteeRole.CHAIR, "Executive"),
            CommitteeMember("M2", "Bob", CommitteeRole.HEAD_OF_ENGINEERING, "Engineering"),
            CommitteeMember("M3", "Charlie", CommitteeRole.HEAD_OF_QA, "QA"),
            CommitteeMember("M4", "David", CommitteeRole.HEAD_OF_SECURITY, "Security"),
        ]
    )
    # Total 4 members. Threshold 0.5 requires >= 2 votes.
    # Only 1 vote cast -> Quorum fails even if 100% Aye!
    mock_votes = [
        Vote(
            id="v1",
            proposal_id="p1",
            voter_id="M1",
            voter_name="Alice",
            voter_role="Chair",
            department="Executive",
            choice=VoteChoice.AYE,
            weight=1.0,
        )
    ]
    tally = committee.tally_votes(mock_votes, approval_threshold=0.5, quorum_threshold=0.5)
    assert tally["quorum_reached"] is False
    assert tally["quorum_percentage"] == 25.0
    assert tally["is_approved"] is False
    assert "Quorum not reached" in tally["rejection_reason"]


def test_boardroom_committee_tally_votes_majority_approval():
    committee = BoardroomCommittee(
        members=[
            CommitteeMember("M1", "Alice", CommitteeRole.CHAIR, "Executive", voting_weight=1.0),
            CommitteeMember("M2", "Bob", CommitteeRole.HEAD_OF_ENGINEERING, "Engineering", voting_weight=1.0),
            CommitteeMember("M3", "Charlie", CommitteeRole.HEAD_OF_QA, "QA", voting_weight=1.0),
            CommitteeMember("M4", "David", CommitteeRole.HEAD_OF_SECURITY, "Security", voting_weight=1.0),
        ]
    )
    # 3 votes: 2 Aye, 1 Nay -> 66.7% Aye, Quorum 75% -> Approved!
    mock_votes = [
        Vote(
            id="v1", proposal_id="p1", voter_id="M1", voter_name="Alice",
            voter_role="Chair", department="Executive", choice=VoteChoice.AYE, weight=1.0
        ),
        Vote(
            id="v2", proposal_id="p1", voter_id="M2", voter_name="Bob",
            voter_role="Eng", department="Engineering", choice=VoteChoice.AYE, weight=1.0
        ),
        Vote(
            id="v3", proposal_id="p1", voter_id="M4", voter_name="David",
            voter_role="Sec", department="Security", choice=VoteChoice.NAY, weight=1.0
        ),
    ]
    tally = committee.tally_votes(mock_votes, approval_threshold=0.5, quorum_threshold=0.5)
    assert tally["quorum_reached"] is True
    assert tally["votes_cast"] == 3
    assert tally["aye_count"] == 2
    assert tally["nay_count"] == 1
    assert tally["is_approved"] is True


@pytest.mark.asyncio
async def test_decision_ledger_sha256_hash_chaining_and_tamper_detection(async_session: AsyncSession):
    ledger_repo = DecisionLedgerRepository(async_session)
    prop_repo = ProposalRepository(async_session)

    # Create dummy proposals
    p1 = await prop_repo.create(
        proposal_id="PROP-LED-01",
        title="Infrastructure Upgrade",
        description="Upgrade TigerVNC to 1.14"
    )
    p2 = await prop_repo.create(
        proposal_id="PROP-LED-02",
        title="Zero-Trust Seccomp",
        description="Lockdown system calls"
    )

    # 1. Append Genesis Entry (Sequence 1)
    e1 = await ledger_repo.append_entry(
        proposal_id=p1.proposal_id,
        decision="APPROVED",
        title=p1.title,
        proposal_type=p1.proposal_type.value,
        proposer_id="BossAI",
        votes_summary={"aye": 4, "nay": 0},
        rationale="Unanimous approval"
    )
    assert e1.sequence_number == 1
    assert e1.previous_hash == "0" * 64
    assert len(e1.entry_hash) == 64

    # 2. Append Second Entry (Sequence 2)
    e2 = await ledger_repo.append_entry(
        proposal_id=p2.proposal_id,
        decision="APPROVED",
        title=p2.title,
        proposal_type=p2.proposal_type.value,
        proposer_id="HEAD-SEC",
        votes_summary={"aye": 3, "nay": 1},
        rationale="Approved with minor caveats"
    )
    assert e2.sequence_number == 2
    assert e2.previous_hash == e1.entry_hash

    # 3. Verify Chain Integrity -> Must pass!
    is_valid, err = await ledger_repo.verify_chain_integrity()
    assert is_valid is True
    assert err is None

    # 4. Simulate Malicious Tampering: Mutate entry 1's decision in the database
    e1.decision = "REJECTED"
    await async_session.commit()

    # 5. Verify Chain Integrity -> Must detect hash tampering!
    is_valid, err = await ledger_repo.verify_chain_integrity()
    assert is_valid is False
    assert "Entry hash mismatch at sequence 1" in str(err)


@pytest.mark.asyncio
async def test_deliberation_engine_departmental_perspectives(async_session: AsyncSession):
    prop_repo = ProposalRepository(async_session)
    proposal = await prop_repo.create(
        proposal_id="PROP-DELIB-01",
        title="Scale Container Concurrency to 20",
        description="Allow 20 parallel employee containers with VNC desktops",
        proposal_type=ProposalType.STRATEGIC_INITIATIVE,
        parameters={"budget_usd": 120.0}
    )

    engine = DeliberationEngine()
    committee = BoardroomCommittee()

    statements = await engine.run_deliberation_round(
        proposal=proposal,
        members=committee.get_members()
    )

    assert len(statements) == 7
    dept_map = {s["department"]: s for s in statements}

    # Security requires containment
    assert "Security" in dept_map
    sec_arg = dept_map["Security"]["argument"].lower()
    assert "sandboxing" in sec_arg or "isolation" in sec_arg

    # QA enforces receipts
    assert "Quality Assurance" in dept_map
    assert "empirical test verification" in dept_map["Quality Assurance"]["argument"].lower()

    # Finance flags high budget
    assert "Finance" in dept_map
    assert dept_map["Finance"]["stance"] in ("neutral", "support")


@pytest.mark.asyncio
async def test_boardroom_governance_engine_full_lifecycle(async_session: AsyncSession):
    prop_repo = ProposalRepository(async_session)
    vote_repo = VoteRepository(async_session)
    ledger_repo = DecisionLedgerRepository(async_session)
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    audit_repo = AuditRepository(async_session)

    # Committee with 3 members
    committee = BoardroomCommittee(
        members=[
            CommitteeMember("BossAI", "CEO", CommitteeRole.CHAIR, "Executive"),
            CommitteeMember("EMP-ENG", "Alex", CommitteeRole.HEAD_OF_ENGINEERING, "Engineering"),
            CommitteeMember("EMP-QA", "Morgan", CommitteeRole.HEAD_OF_QA, "Quality Assurance"),
        ]
    )

    gov_engine = BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=ledger_repo,
        committee=committee,
        task_repo=task_repo,
        project_repo=proj_repo,
        audit_repo=audit_repo
    )

    # 1. Create Proposal
    proposal = await gov_engine.create_proposal(
        title="Deploy Autonomous Consensus Engine",
        description="Implement multi-agent boardroom governance with immutable ledger",
        proposal_type=ProposalType.STRATEGIC_INITIATIVE,
        quorum_threshold=0.6,
        approval_threshold=0.5
    )
    assert proposal.status == ProposalStatus.DISCUSSION

    # 2. Run Deliberation
    proposal, statements = await gov_engine.run_deliberation(proposal.proposal_id)
    assert len(statements) == 3
    assert len(proposal.deliberation_history) == 3

    # 3. Open Voting
    proposal = await gov_engine.open_voting(proposal.proposal_id)
    assert proposal.status == ProposalStatus.VOTING

    # 4. Cast Votes (2 Aye, 1 Nay -> 66.7% Aye, 100% quorum)
    await gov_engine.cast_vote(
        proposal_id=proposal.proposal_id,
        voter_id="BossAI",
        choice=VoteChoice.AYE,
        rationale="Critical company milestone"
    )
    await gov_engine.cast_vote(
        proposal_id=proposal.proposal_id,
        voter_id="EMP-ENG",
        choice=VoteChoice.AYE,
        rationale="Architecture is clean and testable"
    )
    await gov_engine.cast_vote(
        proposal_id=proposal.proposal_id,
        voter_id="EMP-QA",
        choice=VoteChoice.NAY,
        rationale="Requires more integration tests"
    )

    # 5. Finalize Voting -> APPROVED
    final_prop, ledger_entry = await gov_engine.finalize_voting(proposal.proposal_id)
    assert final_prop.status == ProposalStatus.APPROVED
    assert ledger_entry.decision == "APPROVED"
    assert ledger_entry.sequence_number == 1

    # 6. Execute Proposal -> Creates DAG Tasks
    executed_prop, created_tasks = await gov_engine.execute_proposal(proposal.proposal_id)
    assert executed_prop.status == ProposalStatus.EXECUTED
    assert len(created_tasks) == 2
    assert executed_prop.execution_receipt["status"] == "success"
    assert len(executed_prop.execution_receipt["task_ids"]) == 2

    # 7. Decision Ledger Chain is intact
    is_valid, err = await gov_engine.ledger.verify_integrity()
    assert is_valid is True
    assert err is None


@pytest.mark.asyncio
async def test_boss_ai_executive_veto_override(async_session: AsyncSession):
    prop_repo = ProposalRepository(async_session)
    vote_repo = VoteRepository(async_session)
    ledger_repo = DecisionLedgerRepository(async_session)

    gov_engine = BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=ledger_repo,
    )

    proposal = await gov_engine.create_proposal(
        title="High Risk Unrestricted Shell Execution",
        description="Allow employees direct unconfined host terminal execution",
        proposal_type=ProposalType.POLICY_UPDATE
    )

    # Boss AI exercises executive sovereignty to protect sandbox envelope
    vetoed_prop, ledger_entry = await gov_engine.veto_proposal(
        proposal_id=proposal.proposal_id,
        reason="Violates Zero-Trust sandbox isolation principle",
        vetoed_by="BossAI"
    )

    assert vetoed_prop.status == ProposalStatus.VETOED
    assert ledger_entry.decision == "VETOED"
    assert "Violates Zero-Trust sandbox" in ledger_entry.rationale

    # Cannot transition or execute a vetoed proposal
    with pytest.raises(ValueError) as exc:
        await gov_engine.execute_proposal(proposal.proposal_id)
    assert "must be APPROVED" in str(exc.value)


@pytest.mark.asyncio
async def test_decision_ledger_linked_on_proposal_execution(async_session: AsyncSession):
    prop_repo = ProposalRepository(async_session)
    vote_repo = VoteRepository(async_session)
    ledger_repo = DecisionLedgerRepository(async_session)
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)

    gov_engine = BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=ledger_repo,
        task_repo=task_repo,
        project_repo=proj_repo,
    )

    prop = await gov_engine.create_proposal(
        title="Execution Receipt Ledger Link Test",
        description="Verify ledger entry gets execution receipt populated",
        proposal_type=ProposalType.STRATEGIC_INITIATIVE,
        quorum_threshold=0.1,
    )
    await gov_engine.open_voting(prop.proposal_id)
    await gov_engine.cast_vote(prop.proposal_id, "BossAI", VoteChoice.AYE)
    await gov_engine.finalize_voting(prop.proposal_id)

    # Execute proposal
    executed_prop, tasks = await gov_engine.execute_proposal(prop.proposal_id)
    assert executed_prop.status == ProposalStatus.EXECUTED
    assert executed_prop.project_id is not None
    assert len(tasks) >= 1

    # Verify project_id is persisted in database
    refreshed_prop = await prop_repo.get_by_proposal_id(prop.proposal_id)
    assert refreshed_prop.project_id == executed_prop.project_id

    # Verify decision ledger entry has execution_receipt linked!
    ledger_entry = await ledger_repo.get_by_proposal_id(prop.proposal_id)
    assert ledger_entry is not None
    assert ledger_entry.execution_receipt is not None
    assert ledger_entry.execution_receipt["status"] == "success"
    assert ledger_entry.execution_receipt["project_id"] == executed_prop.project_id

    # Verify hash chain integrity holds
    is_valid, err = await ledger_repo.verify_chain_integrity()
    assert is_valid is True
    assert err is None


@pytest.mark.asyncio
async def test_executive_veto_after_approval_creates_second_ledger_entry(async_session: AsyncSession):
    prop_repo = ProposalRepository(async_session)
    vote_repo = VoteRepository(async_session)
    ledger_repo = DecisionLedgerRepository(async_session)

    gov_engine = BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=ledger_repo,
    )

    prop = await gov_engine.create_proposal(
        title="Late Discovered Flaw Proposal",
        description="Proposal that gets approved but vetoed prior to execution",
        quorum_threshold=0.1,
    )
    await gov_engine.open_voting(prop.proposal_id)
    await gov_engine.cast_vote(prop.proposal_id, "BossAI", VoteChoice.AYE)
    approved_prop, first_ledger = await gov_engine.finalize_voting(prop.proposal_id)
    assert approved_prop.status == ProposalStatus.APPROVED
    assert first_ledger.decision == "APPROVED"

    # Boss AI vetoes after approval before execution!
    vetoed_prop, second_ledger = await gov_engine.veto_proposal(
        prop.proposal_id,
        reason="Critical 0-day discovered in proposed dependency"
    )
    assert vetoed_prop.status == ProposalStatus.VETOED
    assert second_ledger.decision == "VETOED"
    assert second_ledger.sequence_number == first_ledger.sequence_number + 1
    assert second_ledger.previous_hash == first_ledger.entry_hash

    # Verify both ledger entries exist in proposal's audit trail
    all_entries = await ledger_repo.list_by_proposal(prop.proposal_id)
    assert len(all_entries) == 2
    assert all_entries[0].decision == "APPROVED"
    assert all_entries[1].decision == "VETOED"

    # Verify hash chain integrity
    is_valid, err = await ledger_repo.verify_chain_integrity()
    assert is_valid is True
    assert err is None


@pytest.mark.asyncio
async def test_unauthorized_voter_rejected(async_session: AsyncSession):
    prop_repo = ProposalRepository(async_session)
    vote_repo = VoteRepository(async_session)
    ledger_repo = DecisionLedgerRepository(async_session)

    gov_engine = BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=ledger_repo,
    )

    prop = await gov_engine.create_proposal(title="Security Test", description="Testing voter authorization")
    await gov_engine.open_voting(prop.proposal_id)

    # Random unauthorized actor cannot vote
    with pytest.raises(ValueError) as exc:
        await gov_engine.cast_vote(
            proposal_id=prop.proposal_id,
            voter_id="MALICIOUS_ACTOR_99",
            choice=VoteChoice.AYE,
        )
    assert "not an active member of the Boardroom Committee" in str(exc.value)


@pytest.mark.asyncio
async def test_proposal_cancellation_lifecycle(async_session: AsyncSession):
    prop_repo = ProposalRepository(async_session)
    vote_repo = VoteRepository(async_session)
    ledger_repo = DecisionLedgerRepository(async_session)

    gov_engine = BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=ledger_repo,
    )

    # 1. Cancel from DISCUSSION
    p1 = await gov_engine.create_proposal(
        title="Cancelled Initiative 1",
        description="Will be cancelled during discussion",
        auto_discuss=True,
    )
    cancelled1, ledger1 = await gov_engine.cancel_proposal(
        proposal_id=p1.proposal_id,
        reason="Priorities shifted to customer incident",
        cancelled_by="BossAI",
    )
    assert cancelled1.status == ProposalStatus.CANCELLED
    assert ledger1.decision == "CANCELLED"
    assert "Priorities shifted" in ledger1.rationale

    # 2. Cancel from VOTING
    p2 = await gov_engine.create_proposal(
        title="Cancelled Initiative 2",
        description="Will be cancelled during active vote",
    )
    await gov_engine.open_voting(p2.proposal_id)
    cancelled2, ledger2 = await gov_engine.cancel_proposal(
        proposal_id=p2.proposal_id,
        reason="Withdrawn by author due to budget reassessment",
        cancelled_by="HEAD-ENG",
    )
    assert cancelled2.status == ProposalStatus.CANCELLED
    assert ledger2.decision == "CANCELLED"

    # Terminal state cannot be cancelled again
    with pytest.raises(ValueError) as exc:
        await gov_engine.cancel_proposal(p2.proposal_id, reason="Redundant cancel")
    assert "terminal state" in str(exc.value)


@pytest.mark.asyncio
async def test_vote_upsert_and_deduplication(async_session: AsyncSession):
    prop_repo = ProposalRepository(async_session)
    vote_repo = VoteRepository(async_session)
    ledger_repo = DecisionLedgerRepository(async_session)

    committee = BoardroomCommittee(
        members=[
            CommitteeMember("M1", "Alice", CommitteeRole.CHAIR, "Executive"),
            CommitteeMember("M2", "Bob", CommitteeRole.HEAD_OF_ENGINEERING, "Engineering"),
        ]
    )

    gov_engine = BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=ledger_repo,
        committee=committee,
    )

    prop = await gov_engine.create_proposal(title="Vote Change Test", description="Test changing a vote")
    await gov_engine.open_voting(prop.proposal_id)

    # M1 votes NAY first
    await gov_engine.cast_vote(prop.proposal_id, "M1", VoteChoice.NAY, rationale="Initial concern")
    votes_first = await vote_repo.list_by_proposal(prop.id)
    assert len(votes_first) == 1
    assert votes_first[0].choice == VoteChoice.NAY

    # M1 changes mind after explanation and votes AYE!
    await gov_engine.cast_vote(prop.proposal_id, "M1", VoteChoice.AYE, rationale="Concerns addressed")
    votes_second = await vote_repo.list_by_proposal(prop.id)
    # Must still be exactly 1 vote record (upsert), not 2!
    assert len(votes_second) == 1
    assert votes_second[0].choice == VoteChoice.AYE
    assert votes_second[0].rationale == "Concerns addressed"


@pytest.mark.asyncio
async def test_concurrent_ledger_appends_no_collisions(async_session: AsyncSession):
    import asyncio
    ledger_repo = DecisionLedgerRepository(async_session)
    prop_repo = ProposalRepository(async_session)

    proposals = []
    for i in range(5):
        p = await prop_repo.create(
            proposal_id=f"PROP-CONC-{i}",
            title=f"Concurrent Proposal {i}",
            description="Testing concurrent ledger appends"
        )
        proposals.append(p)

    # Concurrently append 5 decisions to the ledger
    tasks = [
        ledger_repo.append_entry(
            proposal_id=p.proposal_id,
            decision="APPROVED",
            title=p.title,
            proposal_type=p.proposal_type.value,
            proposer_id="BossAI",
            votes_summary={"aye": 3, "nay": 0},
            rationale=f"Concurrent decision {idx}"
        )
        for idx, p in enumerate(proposals)
    ]
    entries = await asyncio.gather(*tasks)
    assert len(entries) == 5

    # Verify all sequence numbers are distinct and sequential 1..5
    seqs = sorted([e.sequence_number for e in entries])
    assert seqs == list(range(1, 6))

    # Verify chain integrity across all entries
    is_valid, err = await ledger_repo.verify_chain_integrity()
    assert is_valid is True
    assert err is None

