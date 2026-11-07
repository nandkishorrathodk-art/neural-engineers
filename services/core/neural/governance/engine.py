from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from neural.constants import (
    ProposalStatus,
    ProposalType,
    TaskPriority,
    TaskStatus,
    VoteChoice,
)
from neural.db.models import DecisionLedgerEntry, Proposal, Task, Vote
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
from neural.governance.ledger import DecisionLedger
from neural.governance.proposals import ProposalStateMachine
from neural.logger import get_logger

logger = get_logger("ancos.governance.engine")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class BoardroomGovernanceEngine:
    """
    Boardroom Governance & Consensus Deliberation Engine.
    Coordinates proposals, committee deliberation, quorum-enforced voting,
    immutable decision ledger recording, and DAG task execution handoff.
    """

    def __init__(
        self,
        proposal_repo: ProposalRepository,
        vote_repo: VoteRepository,
        ledger_repo: DecisionLedgerRepository,
        committee: BoardroomCommittee | None = None,
        deliberation_engine: DeliberationEngine | None = None,
        task_repo: TaskRepository | None = None,
        project_repo: ProjectRepository | None = None,
        message_bus: Any | None = None,
        audit_repo: AuditRepository | None = None,
    ):
        self.proposal_repo = proposal_repo
        self.vote_repo = vote_repo
        self.ledger_repo = ledger_repo
        self.committee = committee or BoardroomCommittee()
        self.deliberation_engine = deliberation_engine or DeliberationEngine(message_bus=message_bus)
        self.ledger = DecisionLedger(ledger_repo)
        self.task_repo = task_repo
        self.project_repo = project_repo
        self.message_bus = message_bus
        self.audit_repo = audit_repo

    async def create_proposal(
        self,
        title: str,
        description: str,
        proposal_type: ProposalType = ProposalType.STRATEGIC_INITIATIVE,
        proposer_id: str = "BossAI",
        proposer_role: str = "Chief Architect / CEO",
        project_id: str | None = None,
        parameters: dict[str, Any] | None = None,
        quorum_threshold: float = 0.5,
        approval_threshold: float = 0.5,
        auto_discuss: bool = True
    ) -> Proposal:
        """
        Creates a new strategic proposal, optionally transitioning directly into DISCUSSION.
        """
        proposal_id = f"PROP-{uuid.uuid4().hex[:6].upper()}"
        initial_status = ProposalStatus.DISCUSSION if auto_discuss else ProposalStatus.DRAFT

        proposal = await self.proposal_repo.create(
            proposal_id=proposal_id,
            title=title,
            description=description,
            proposal_type=proposal_type,
            status=initial_status,
            proposer_id=proposer_id,
            proposer_role=proposer_role,
            project_id=project_id,
            parameters=parameters or {},
            quorum_threshold=quorum_threshold,
            approval_threshold=approval_threshold
        )

        logger.info(
            "Governance proposal created",
            proposal_id=proposal_id,
            title=title,
            type=proposal_type.value,
            status=initial_status.value
        )

        if self.message_bus:
            try:
                await self.message_bus.publish(
                    channel="boardroom:proposals",
                    message={
                        "event": "PROPOSAL_CREATED",
                        "proposal_id": proposal.proposal_id,
                        "title": proposal.title,
                        "type": proposal.proposal_type.value,
                        "proposer": proposal.proposer_id,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Bus publish failed", error=str(e))

        if self.audit_repo:
            await self.audit_repo.log(
                actor=proposer_id,
                action="CREATE_PROPOSAL",
                resource_type="Proposal",
                resource_id=proposal.proposal_id,
                details={"title": title, "type": proposal_type.value}
            )

        return proposal

    async def run_deliberation(
        self,
        proposal_id: str,
        members: Sequence[CommitteeMember] | None = None
    ) -> tuple[Proposal, list[dict[str, Any]]]:
        """
        Runs a structured deliberation round across Department Heads.
        """
        proposal = await self.proposal_repo.get_by_proposal_id(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found")

        if not ProposalStateMachine.can_deliberate(proposal.status):
            raise ValueError(
                f"Cannot deliberate on proposal in state '{proposal.status.value}'"
            )

        # Ensure status is DISCUSSION
        if proposal.status == ProposalStatus.DRAFT:
            proposal = await self.proposal_repo.update_status(
                proposal_id=proposal.proposal_id,
                status=ProposalStatus.DISCUSSION
            )

        active_members = members or self.committee.get_members(active_only=True)
        statements = await self.deliberation_engine.run_deliberation_round(
            proposal=proposal,
            members=active_members,
            db_session=getattr(self.proposal_repo, "session", None),
        )

        proposal = await self.proposal_repo.add_deliberation_statements(
            proposal_id=proposal.proposal_id,
            statements=statements
        )

        return proposal, statements

    async def open_voting(self, proposal_id: str) -> Proposal:
        """
        Transitions proposal from DISCUSSION to VOTING.
        """
        proposal = await self.proposal_repo.get_by_proposal_id(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found")

        new_status = ProposalStateMachine.transition(proposal.status, ProposalStatus.VOTING)
        updated = await self.proposal_repo.update_status(proposal.proposal_id, new_status)

        if self.message_bus:
            try:
                await self.message_bus.publish(
                    channel="boardroom:proposals",
                    message={
                        "event": "VOTING_OPENED",
                        "proposal_id": proposal.proposal_id,
                        "title": proposal.title,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Bus publish failed", error=str(e))

        return updated

    async def cast_vote(
        self,
        proposal_id: str,
        voter_id: str,
        choice: VoteChoice,
        voter_name: str | None = None,
        voter_role: str | None = None,
        department: str | None = None,
        weight: float | None = None,
        rationale: str | None = None
    ) -> Vote:
        """
        Records a vote from an authorized committee member / department head.
        """
        proposal = await self.proposal_repo.get_by_proposal_id(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found")

        if not ProposalStateMachine.can_vote(proposal.status):
            raise ValueError(
                f"Voting is not open for proposal '{proposal_id}' (current status: {proposal.status.value})"
            )

        # Validate voter is an authorized active committee member
        member = self.committee.get_member(voter_id)
        if not member or not member.is_active:
            raise ValueError(
                f"Voter '{voter_id}' is not an active member of the Boardroom Committee"
            )
        v_name = voter_name or member.name
        v_role = voter_role or member.role.value
        v_dept = department or member.department
        v_weight = weight if weight is not None else member.voting_weight

        vote = await self.vote_repo.cast_vote(
            proposal_id=proposal.id,
            voter_id=voter_id,
            voter_name=v_name,
            voter_role=v_role,
            department=v_dept,
            choice=choice,
            weight=v_weight,
            rationale=rationale
        )

        if self.message_bus:
            try:
                await self.message_bus.publish(
                    channel="boardroom:votes",
                    message={
                        "event": "VOTE_CAST",
                        "proposal_id": proposal.proposal_id,
                        "voter": v_name,
                        "choice": choice.value,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Bus publish failed", error=str(e))

        return vote

    async def finalize_voting(
        self,
        proposal_id: str,
        force_decision: str | None = None
    ) -> tuple[Proposal, DecisionLedgerEntry]:
        """
        Tallies votes, verifies quorum, computes outcome, and writes to immutable Decision Ledger.
        """
        proposal = await self.proposal_repo.get_by_proposal_id(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found")

        if proposal.status != ProposalStatus.VOTING:
            raise ValueError(
                f"Proposal '{proposal_id}' is not in VOTING state (status: {proposal.status.value})"
            )

        votes = await self.vote_repo.list_by_proposal(proposal.id)
        tally = self.committee.tally_votes(
            votes=votes,
            approval_threshold=proposal.approval_threshold,
            quorum_threshold=proposal.quorum_threshold
        )

        if force_decision:
            is_approved = force_decision.upper() == "APPROVED"
        else:
            is_approved = tally["is_approved"]

        target_status = ProposalStatus.APPROVED if is_approved else ProposalStatus.REJECTED
        decision_str = "APPROVED" if is_approved else "REJECTED"

        # Update proposal
        updated_proposal = await self.proposal_repo.update_status(
            proposal_id=proposal.proposal_id,
            status=target_status,
            resolved_at=utc_now()
        )

        rationale = (
            f"Approved by Boardroom vote: {tally['weighted_aye']} Aye vs {tally['weighted_nay']} Nay "
            f"(Quorum: {tally['quorum_percentage']}%)."
            if is_approved
            else f"Rejected by Boardroom vote: {tally['rejection_reason']}."
        )

        # Record in Immutable Hash-Chained Decision Ledger
        ledger_entry = await self.ledger.record_decision(
            proposal_id=proposal.proposal_id,
            decision=decision_str,
            title=proposal.title,
            proposal_type=proposal.proposal_type.value,
            proposer_id=proposal.proposer_id,
            votes_summary=tally,
            rationale=rationale
        )

        if self.message_bus:
            try:
                await self.message_bus.publish(
                    channel="boardroom:decisions",
                    message={
                        "event": "PROPOSAL_FINALIZED",
                        "proposal_id": proposal.proposal_id,
                        "decision": decision_str,
                        "ledger_entry_id": ledger_entry.entry_id,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Bus publish failed", error=str(e))

        return updated_proposal, ledger_entry

    async def veto_proposal(
        self,
        proposal_id: str,
        reason: str,
        vetoed_by: str = "BossAI"
    ) -> tuple[Proposal, DecisionLedgerEntry]:
        """
        Executive Veto power (Boss AI / Human Owner).
        Immediately terminates deliberation/voting and marks proposal VETOED in Decision Ledger.
        """
        proposal = await self.proposal_repo.get_by_proposal_id(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found")

        if ProposalStateMachine.is_terminal(proposal.status):
            raise ValueError(
                f"Cannot veto proposal '{proposal_id}' in terminal state '{proposal.status.value}'"
            )

        updated_proposal = await self.proposal_repo.update_status(
            proposal_id=proposal.proposal_id,
            status=ProposalStatus.VETOED,
            resolved_at=utc_now()
        )

        ledger_entry = await self.ledger.record_decision(
            proposal_id=proposal.proposal_id,
            decision="VETOED",
            title=proposal.title,
            proposal_type=proposal.proposal_type.value,
            proposer_id=proposal.proposer_id,
            votes_summary={"vetoed_by": vetoed_by, "reason": reason},
            rationale=f"Executive Veto executed by {vetoed_by}: {reason}"
        )

        if self.message_bus:
            try:
                await self.message_bus.publish(
                    channel="boardroom:decisions",
                    message={
                        "event": "PROPOSAL_VETOED",
                        "proposal_id": proposal.proposal_id,
                        "vetoed_by": vetoed_by,
                        "reason": reason,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Bus publish failed", error=str(e))

        return updated_proposal, ledger_entry

    async def cancel_proposal(
        self,
        proposal_id: str,
        reason: str,
        cancelled_by: str = "BossAI"
    ) -> tuple[Proposal, DecisionLedgerEntry]:
        """
        Cancels an active proposal in DRAFT, DISCUSSION, or VOTING state.
        Records cancellation in the Decision Ledger.
        """
        proposal = await self.proposal_repo.get_by_proposal_id(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found")

        if ProposalStateMachine.is_terminal(proposal.status):
            raise ValueError(
                f"Cannot cancel proposal '{proposal_id}' in terminal state '{proposal.status.value}'"
            )

        new_status = ProposalStateMachine.transition(proposal.status, ProposalStatus.CANCELLED)
        updated_proposal = await self.proposal_repo.update_status(
            proposal_id=proposal.proposal_id,
            status=new_status,
            resolved_at=utc_now()
        )

        ledger_entry = await self.ledger.record_decision(
            proposal_id=proposal.proposal_id,
            decision="CANCELLED",
            title=proposal.title,
            proposal_type=proposal.proposal_type.value,
            proposer_id=proposal.proposer_id,
            votes_summary={"cancelled_by": cancelled_by, "reason": reason},
            rationale=f"Proposal cancelled by {cancelled_by}: {reason}"
        )

        if self.message_bus:
            try:
                await self.message_bus.publish(
                    channel="boardroom:decisions",
                    message={
                        "event": "PROPOSAL_CANCELLED",
                        "proposal_id": proposal.proposal_id,
                        "cancelled_by": cancelled_by,
                        "reason": reason,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Bus publish failed", error=str(e))

        return updated_proposal, ledger_entry

    async def execute_proposal(
        self,
        proposal_id: str
    ) -> tuple[Proposal, list[Task]]:
        """
        Executes an APPROVED proposal:
        Spawns corresponding tasks into the Task DAG scheduler / repository,
        updates the proposal execution receipt, and links the receipt into the decision ledger.
        """
        proposal = await self.proposal_repo.get_by_proposal_id(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal '{proposal_id}' not found")

        if proposal.status != ProposalStatus.APPROVED:
            raise ValueError(
                f"Proposal '{proposal_id}' must be APPROVED before execution (status: {proposal.status.value})"
            )

        created_tasks: list[Task] = []
        params = proposal.parameters or {}

        # 1. Resolve or create Project
        project_uuid = proposal.project_id
        if not project_uuid and self.project_repo:
            new_proj = await self.project_repo.create(
                name=f"Project: {proposal.title}",
                objective=proposal.description
            )
            project_uuid = new_proj.id
            proposal.project_id = project_uuid

        # 2. Spawn DAG Tasks if specified or strategic
        raw_tasks = params.get("tasks", [])
        if not raw_tasks and proposal.proposal_type in {
            ProposalType.STRATEGIC_INITIATIVE,
            ProposalType.ARCHITECTURE_CHANGE,
            ProposalType.TASK_ORCHESTRATION,
        }:
            # Default task decomposition
            raw_tasks = [
                {
                    "title": f"Design & Blueprint: {proposal.title}",
                    "objective": f"Architecture specification for {proposal.title}",
                    "scope": "Architecture & Specs",
                    "acceptance_criteria": "Approved blueprint specification and test cases",
                    "priority": TaskPriority.HIGH,
                },
                {
                    "title": f"Implementation & Rollout: {proposal.title}",
                    "objective": f"Deploy core components for {proposal.title}",
                    "scope": "Core Engineering",
                    "acceptance_criteria": "Working implementation with unit tests",
                    "priority": TaskPriority.HIGH,
                },
            ]

        if self.task_repo and project_uuid:
            for t_idx, t_data in enumerate(raw_tasks, start=1):
                t_obj = await self.task_repo.create(
                    task_id=f"TASK-GOV-{proposal.proposal_id[-4:]}-{t_idx:02d}",
                    project_id=project_uuid,
                    title=t_data.get("title", f"Task {t_idx} for {proposal.title}"),
                    objective=t_data.get("objective", proposal.description),
                    scope=t_data.get("scope", "Governance Execution"),
                    acceptance_criteria=t_data.get("acceptance_criteria", "Pass verification"),
                    priority=t_data.get("priority", TaskPriority.HIGH),
                    status=TaskStatus.PENDING,
                )
                created_tasks.append(t_obj)

        receipt = {
            "status": "success",
            "executed_at": utc_now().isoformat(),
            "tasks_created_count": len(created_tasks),
            "task_ids": [t.task_id for t in created_tasks],
            "project_id": project_uuid,
        }

        updated_proposal = await self.proposal_repo.set_execution_receipt(
            proposal_id=proposal.proposal_id,
            receipt=receipt,
            project_id=project_uuid
        )

        # Link receipt into the immutable decision ledger
        await self.ledger_repo.update_execution_receipt(
            proposal_id=proposal.proposal_id,
            receipt=receipt
        )

        if self.message_bus:
            try:
                await self.message_bus.publish(
                    channel="boardroom:execution",
                    message={
                        "event": "PROPOSAL_EXECUTED",
                        "proposal_id": proposal.proposal_id,
                        "tasks_created": [t.task_id for t in created_tasks],
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Bus publish failed", error=str(e))

        return updated_proposal, created_tasks
