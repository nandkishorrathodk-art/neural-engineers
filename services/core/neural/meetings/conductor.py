import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from neural.constants import (
    MeetingStatus,
    MeetingType,
    ProposalStatus,
    ProposalType,
    TaskStatus,
    VoteChoice,
)
from neural.db.models import ActionItem, DecisionLedgerEntry, Meeting, Proposal, Task
from neural.db.repositories import (
    ActionItemRepository,
    DecisionLedgerRepository,
    MeetingRepository,
    ProjectRepository,
    ProposalRepository,
    TaskRepository,
    VoteRepository,
)
from neural.exceptions import (
    FabricatedProgressError,
    MeetingPolicyViolationError,
)
from neural.governance.engine import BoardroomGovernanceEngine
from neural.logger import get_logger
from neural.meetings.actions import ActionItemTracker
from neural.meetings.minutes import MeetingMinutesGenerator
from neural.meetings.participants import ParticipantSelector
from neural.meetings.policy import MeetingPolicyConfig, MeetingPolicyEnforcer
from neural.meetings.reports import (
    FactGroundedProgressReport,
    ProgressReportCompiler,
    StructuredProgressClaim,
    validate_report_data,
)

logger = get_logger("ancos.meetings.conductor")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MeetingConductor:
    """
    Autonomous Meeting & Boardroom Conductor.
    Orchestrates the complete boardroom lifecycle: dynamic scheduling,
    live session moderation, fact-grounded report collection,
    explicit decision rationales, automated minutes, and action item tracking.
    """

    def __init__(
        self,
        meeting_repo: MeetingRepository,
        action_item_repo: ActionItemRepository | None = None,
        participant_selector: ParticipantSelector | None = None,
        message_bus: Any | None = None,
        task_repo: TaskRepository | None = None,
        project_repo: ProjectRepository | None = None,
        governance_engine: BoardroomGovernanceEngine | None = None,
        policy_config: MeetingPolicyConfig | None = None,
    ):
        self.meeting_repo = meeting_repo
        self.action_item_repo = action_item_repo or ActionItemRepository(meeting_repo.session)
        self.action_tracker = ActionItemTracker(self.action_item_repo)
        self.participant_selector = participant_selector
        self.message_bus = message_bus
        self.task_repo = task_repo or TaskRepository(meeting_repo.session)
        self.project_repo = project_repo or ProjectRepository(meeting_repo.session)
        self.policy_config = policy_config or MeetingPolicyConfig()
        self.policy_enforcer = MeetingPolicyEnforcer(self.policy_config)

        if governance_engine:
            self.governance_engine = governance_engine
        else:
            self.governance_engine = BoardroomGovernanceEngine(
                proposal_repo=ProposalRepository(meeting_repo.session),
                vote_repo=VoteRepository(meeting_repo.session),
                ledger_repo=DecisionLedgerRepository(meeting_repo.session),
                task_repo=self.task_repo,
                project_repo=self.project_repo,
                message_bus=message_bus,
            )

    def _get_or_init_policy_state(self, meeting: Meeting) -> dict[str, Any]:
        minutes = dict(meeting.minutes or {})
        if "policy_state" not in minutes:
            minutes["policy_state"] = MeetingPolicyEnforcer.init_policy_state(
                agenda=meeting.agenda,
                config=self.policy_config,
            )
            meeting.minutes = minutes
        return minutes["policy_state"]

    def _record_activity(
        self,
        meeting: Meeting,
        speaker_id: str,
        event_type: str,
        summary: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Records a timestamped, structured activity event in the meeting minutes."""
        activity = {
            "timestamp": utc_now().isoformat(),
            "speaker_id": speaker_id,
            "event_type": event_type,
            "summary": summary,
            "data": data or {},
        }
        current_minutes = dict(meeting.minutes or {})
        activity_log = list(current_minutes.get("activity_log", []))
        activity_log.append(activity)
        current_minutes["activity_log"] = activity_log
        meeting.minutes = current_minutes
        return activity

    async def schedule_meeting(
        self,
        title: str,
        meeting_type: MeetingType,
        agenda: list[str],
        participants: list[str],
        scheduled_at: datetime | None = None
    ) -> Meeting:
        """
        Schedules a new boardroom meeting.
        """
        meeting_id = f"ANC-MTG-{uuid.uuid4().hex[:4].upper()}"
        meeting = await self.meeting_repo.create(
            meeting_id=meeting_id,
            title=title,
            meeting_type=meeting_type,
            status=MeetingStatus.SCHEDULED,
            agenda=agenda,
            participants=participants,
            minutes={},
            decisions=[],
            scheduled_at=scheduled_at or utc_now(),
            active_speaker_id=None
        )
        logger.info(
            "Meeting scheduled",
            meeting_id=meeting_id,
            title=title,
            type=meeting_type.value,
            participants_count=len(participants)
        )
        return meeting

    async def convene_meeting(self, meeting_id: str) -> Meeting:
        """
        Convenes a scheduled meeting, transitioning it to IN_PROGRESS.
        """
        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found")

        if meeting.status == MeetingStatus.CONCLUDED:
            raise ValueError(f"Meeting {meeting_id} has already concluded and cannot be reconvened")

        if meeting.status == MeetingStatus.IN_PROGRESS:
            # Idempotent replay: already convened and in progress
            return meeting

        meeting.status = MeetingStatus.IN_PROGRESS
        if meeting.started_at is None:
            meeting.started_at = utc_now()

        policy_state = self._get_or_init_policy_state(meeting)
        meeting.current_stage = policy_state.get("current_stage", "CONVENE")

        initial_speaker = meeting.participants[0] if meeting.participants else "BossAI"
        meeting.active_speaker_id = initial_speaker

        self._record_activity(
            meeting=meeting,
            speaker_id=initial_speaker,
            event_type="convened",
            summary=f"Meeting '{meeting.title}' convened and active speaker set to {initial_speaker}",
            data={"current_stage": meeting.current_stage},
        )

        await self.meeting_repo.session.commit()
        await self.meeting_repo.session.refresh(meeting)

        logger.info("Meeting convened and in progress", meeting_id=meeting_id, active_speaker=initial_speaker)
        return meeting

    async def advance_agenda_stage(self, meeting_id: str, target_stage: str) -> Meeting:
        """
        Advances the meeting to the next designated agenda stage in monotonic order.
        """
        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found")

        if meeting.status == MeetingStatus.CONCLUDED:
            raise ValueError(f"Meeting {meeting_id} has already concluded and cannot accept new reports")
        if meeting.status != MeetingStatus.IN_PROGRESS:
            raise ValueError(f"Cannot advance agenda for meeting in status '{meeting.status.value}'")

        policy_state = self._get_or_init_policy_state(meeting)
        old_stage = policy_state.get("current_stage")
        new_stage = self.policy_enforcer.advance_agenda_stage(policy_state, target_stage)
        meeting.current_stage = new_stage

        current_minutes = dict(meeting.minutes or {})
        current_minutes["policy_state"] = policy_state
        meeting.minutes = current_minutes

        if old_stage != new_stage:
            self._record_activity(
                meeting=meeting,
                speaker_id=meeting.active_speaker_id or "BossAI",
                event_type="stage_advanced",
                summary=f"Agenda stage advanced to '{new_stage}'",
                data={"stage": new_stage},
            )
            await self.meeting_repo.session.commit()
            await self.meeting_repo.session.refresh(meeting)
        return meeting

    async def validate_progress_claim(
        self,
        claim: dict[str, Any] | StructuredProgressClaim,
        meeting_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Validates progress claims against repository evidence:
        Evidence-Grounded Progress (Hash != Truth):
        Requires structured evidence matching real tasks, commits, artifacts, and test runs.
        Rejects ungrounded or fabricated claims with FabricatedProgressError.
        """
        if isinstance(claim, StructuredProgressClaim):
            claim_dict = claim.model_dump(mode="json")
        elif isinstance(claim, dict):
            structured = StructuredProgressClaim(**claim)
            claim_dict = structured.model_dump(mode="json")
        else:
            raise FabricatedProgressError("Claim must be a StructuredProgressClaim or dictionary.")

        task_id = claim_dict["task_id"]
        task = await self.task_repo.get_by_task_id(task_id)
        if not task:
            raise FabricatedProgressError(
                f"Progress claim rejected: task '{task_id}' does not exist in repository records."
            )

        if task.status != TaskStatus.COMPLETED:
            raise FabricatedProgressError(
                f"Progress claim rejected: task '{task_id}' status is '{task.status.value}', not COMPLETED. "
                "Only completed deliverables with passing verification may be claimed as progress."
            )

        vr = task.verification_result or {}
        receipt = vr.get("receipt", vr) if isinstance(vr, dict) else {}
        if not receipt:
            raise FabricatedProgressError(
                f"Progress claim rejected: task '{task_id}' has no independent verification receipt."
            )

        # Verification pass check
        is_passed = receipt.get("passed") is True or str(receipt.get("verification_status")).upper() == "PASSED"
        if not is_passed:
            raise FabricatedProgressError(
                f"Progress claim rejected: verification receipt for task '{task_id}' indicates failure."
            )

        # test_run_id check
        valid_run_ids = {
            str(receipt.get("receipt_id", "")),
            str(receipt.get("test_run_id", "")),
            str(receipt.get("attempt_id", "")),
            str(task.id),
            str(task.task_id),
        }
        valid_run_ids.discard("")
        if claim_dict["test_run_id"] not in valid_run_ids:
            raise FabricatedProgressError(
                f"Progress claim rejected: test_run_id '{claim_dict['test_run_id']}' does not match "
                f"verified repository test records ({valid_run_ids})."
            )

        # artifact_hash check
        valid_hashes = set()
        if receipt.get("artifact_digests"):
            valid_hashes.update(receipt["artifact_digests"].values())
        if receipt.get("artifact_hashes"):
            valid_hashes.update(receipt["artifact_hashes"])
        if receipt.get("artifact_hash"):
            valid_hashes.add(receipt["artifact_hash"])
        if receipt.get("signature"):
            valid_hashes.add(receipt["signature"])

        if not valid_hashes:
            raise FabricatedProgressError(
                f"Progress claim rejected: task '{task_id}' has no verified artifact hashes in repository records."
            )
        if claim_dict["artifact_hash"] not in valid_hashes:
            raise FabricatedProgressError(
                f"Progress claim rejected: artifact_hash '{claim_dict['artifact_hash']}' does not match "
                f"verified repository artifact hashes ({valid_hashes})."
            )

        # commit_sha check
        valid_commits = {
            str(receipt.get("commit_sha", "")).lower(),
            str(receipt.get("commit_hash", "")).lower(),
            str(receipt.get("head_commit", "")).lower(),
        }
        valid_commits.discard("")
        if not valid_commits:
            raise FabricatedProgressError(
                f"Progress claim rejected: task '{task_id}' has no verified commit records in repository records."
            )
        if claim_dict["commit_sha"].lower() not in valid_commits:
            raise FabricatedProgressError(
                f"Progress claim rejected: commit_sha '{claim_dict['commit_sha']}' does not match "
                f"verified repository commit ({valid_commits})."
            )

        # If meeting_id is supplied, record verified claim in minutes and activity log
        if meeting_id:
            meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
            if meeting:
                if meeting.status == MeetingStatus.CONCLUDED:
                    raise ValueError(f"Meeting {meeting_id} has already concluded and cannot accept new claims.")

                current_minutes = dict(meeting.minutes or {})
                verified_claims = list(current_minutes.get("verified_claims", []))
                already_verified = any(
                    c.get("task_id") == claim_dict["task_id"]
                    and c.get("artifact_hash") == claim_dict["artifact_hash"]
                    and c.get("commit_sha") == claim_dict["commit_sha"]
                    for c in verified_claims
                )
                if not already_verified:
                    verified_claims.append(claim_dict)
                    current_minutes["verified_claims"] = verified_claims
                    meeting.minutes = current_minutes

                    self._record_activity(
                        meeting=meeting,
                        speaker_id=meeting.active_speaker_id or "BossAI",
                        event_type="progress_claim_verified",
                        summary=f"Progress claim verified for task {task_id}: {claim_dict['claim']}",
                        data=claim_dict,
                    )
                    await self.meeting_repo.session.commit()
                    await self.meeting_repo.session.refresh(meeting)

        return claim_dict

    async def set_speaker(
        self,
        meeting_id: str,
        speaker_id: str,
        reason: str = ""
    ) -> Meeting:
        """
        Dynamically transitions the active speaking floor in an in-progress meeting.
        """
        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found")

        if meeting.status == MeetingStatus.CONCLUDED:
            raise ValueError(f"Meeting {meeting_id} has already concluded and cannot accept new reports")

        if meeting.status != MeetingStatus.IN_PROGRESS:
            raise ValueError(f"Cannot change speaker for meeting in status '{meeting.status.value}'")

        if meeting.participants and speaker_id not in meeting.participants and speaker_id != "BossAI":
            raise ValueError(f"Speaker '{speaker_id}' is not an authorized participant in meeting {meeting_id}")

        previous_speaker = meeting.active_speaker_id
        meeting.active_speaker_id = speaker_id
        summary_text = f"Speaking floor transferred to {speaker_id}"
        if reason:
            summary_text = f"{summary_text} ({reason})"

        self._record_activity(
            meeting=meeting,
            speaker_id=speaker_id,
            event_type="speaker_changed",
            summary=summary_text,
            data={"reason": reason, "previous_speaker_id": previous_speaker},
        )

        await self.meeting_repo.session.commit()
        await self.meeting_repo.session.refresh(meeting)

        if self.message_bus:
            try:
                await self.message_bus.broadcast(
                    title=f"Meeting Speaker: {speaker_id}",
                    content=summary_text
                )
            except Exception as e:
                logger.warning("Could not broadcast speaker event", error=str(e))

        logger.info("Meeting speaker updated", meeting_id=meeting_id, speaker_id=speaker_id)
        return meeting

    async def collect_report(
        self,
        meeting_id: str,
        report_data: FactGroundedProgressReport | dict[str, Any]
    ) -> dict[str, Any]:
        """
        Collects and validates a progress report for an attendee.
        Rejects fabricated percentages and enforces the 4 mandatory questions.
        """
        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found")

        if meeting.status == MeetingStatus.CONCLUDED:
            raise ValueError(f"Meeting {meeting_id} has already concluded and cannot accept new reports")

        if isinstance(report_data, FactGroundedProgressReport):
            validated_dict = report_data.model_dump(mode="json")
        else:
            # Validate raw dict for fabricated percentages and mandatory fields
            validate_report_data(report_data)
            # Compile through compiler to ensure receipt integrity
            compiled = ProgressReportCompiler.compile(
                employee_id=report_data.get("employee_id", "UNKNOWN"),
                task_id=report_data.get("task_id", "UNKNOWN"),
                what_was_done=report_data.get("what_was_done", ""),
                what_remains=report_data.get("what_remains", ""),
                what_is_blocking=report_data.get("what_is_blocking"),
                verification_receipt=report_data.get("verification_receipt", {}),
                raw_payload=report_data
            )
            validated_dict = compiled.model_dump(mode="json")

        emp_id = validated_dict.get("employee_id", "UNKNOWN")
        meeting.active_speaker_id = emp_id

        # Policy controls enforcement: turns, per-speaker token budget, duplicate statements
        current_minutes = dict(meeting.minutes or {})
        policy_state = self._get_or_init_policy_state(meeting)
        turn_statement = f"{validated_dict.get('what_was_done', '')} {validated_dict.get('what_remains', '')}".strip()
        self.policy_enforcer.record_statement(
            policy_state=policy_state,
            speaker_id=emp_id,
            statement=turn_statement,
            tokens=100,
        )
        current_minutes["policy_state"] = policy_state

        # Evidence-Grounded Progress validation if claim or completion is reported
        raw_input = report_data if isinstance(report_data, dict) else {}
        claim_payload = validated_dict.get("progress_claim") or raw_input.get("progress_claim")
        if claim_payload:
            await self.validate_progress_claim(claim_payload, meeting_id=meeting.meeting_id)

        # Record presentation event
        self._record_activity(
            meeting=meeting,
            speaker_id=emp_id,
            event_type="report_presented",
            summary=f"Report presented for task {validated_dict.get('task_id')} (status: {validated_dict.get('factual_status')})",
            data={
                "task_id": validated_dict.get("task_id"),
                "status": validated_dict.get("factual_status"),
                "receipt": validated_dict.get("verification_receipt"),
            },
        )

        # If a blocker is reported, explicitly record a blocker identification activity
        blocker_msg = validated_dict.get("what_is_blocking")
        if blocker_msg and str(blocker_msg).strip().lower() not in ("none", "n/a", "no", "false"):
            self._record_activity(
                meeting=meeting,
                speaker_id=emp_id,
                event_type="blocker_identified",
                summary=f"Blocker identified on task {validated_dict.get('task_id')}: {blocker_msg}",
                data={
                    "task_id": validated_dict.get("task_id"),
                    "blocker": blocker_msg,
                },
            )

        current_minutes = dict(meeting.minutes or {})
        reports_list = list(current_minutes.get("progress_reports", []))
        reports_list.append(validated_dict)
        current_minutes["progress_reports"] = reports_list

        if blocker_msg and str(blocker_msg).strip().lower() not in ("none", "n/a", "no", "false"):
            blockers_list = list(current_minutes.get("blockers_identified", []))
            blockers_list.append({
                "task_id": validated_dict.get("task_id"),
                "employee_id": emp_id,
                "blocker": blocker_msg,
            })
            current_minutes["blockers_identified"] = blockers_list

        meeting.minutes = current_minutes

        await self.meeting_repo.session.commit()
        await self.meeting_repo.session.refresh(meeting)
        return validated_dict

    async def record_decision(
        self,
        meeting_id: str,
        topic: str,
        decision: str,
        rationale: str,
        decided_by: str = "BossAI",
        impact: str = "Standard workflow",
        as_governance: bool = False,
        proposal_type: ProposalType = ProposalType.ARCHITECTURE_CHANGE,
        project_id: str | None = None,
        tasks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Records an explicit decision with its architectural or operational rationale.
        If as_governance is True, routes the decision through ANCOS's formal Governance Engine.
        """
        if as_governance:
            dec_obj, _, _, _ = await self.route_decision_to_governance(
                meeting_id=meeting_id,
                topic=topic,
                description=f"{decision}. Rationale: {rationale}",
                proposal_type=proposal_type,
                proposer_id=decided_by,
                project_id=project_id,
                tasks=tasks,
            )
            return dec_obj

        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found")

        if meeting.status == MeetingStatus.CONCLUDED:
            raise ValueError(f"Meeting {meeting_id} has already concluded and cannot accept new reports")

        decision_obj = {
            "decision_id": f"DEC-{uuid.uuid4().hex[:6].upper()}",
            "topic": topic,
            "decision": decision,
            "rationale": rationale,
            "decided_by": decided_by,
            "impact": impact,
            "timestamp": utc_now().isoformat()
        }

        meeting.active_speaker_id = decided_by
        self._record_activity(
            meeting=meeting,
            speaker_id=decided_by,
            event_type="decision_recorded",
            summary=f"Decision recorded on '{topic}': {decision}",
            data=decision_obj,
        )

        decisions = list(meeting.decisions or [])
        decisions.append(decision_obj)
        meeting.decisions = decisions

        await self.meeting_repo.session.commit()
        await self.meeting_repo.session.refresh(meeting)
        logger.info(
            "Decision recorded",
            meeting_id=meeting_id,
            decision_id=decision_obj["decision_id"],
            topic=topic
        )
        return decision_obj

    async def route_decision_to_governance(
        self,
        meeting_id: str,
        topic: str,
        description: str,
        proposal_type: ProposalType = ProposalType.ARCHITECTURE_CHANGE,
        proposer_id: str = "BossAI",
        project_id: str | None = None,
        tasks: list[dict[str, Any]] | None = None,
        auto_deliberate: bool = True,
        auto_vote: bool = True,
        executive_veto_reason: str | None = None,
    ) -> tuple[dict[str, Any], Proposal, DecisionLedgerEntry, list[Task]]:
        """
        Unified Governance Engine Linkage:
        Routes meeting architectural deliberations through ANCOS's formal Governance Engine:
        Meeting Discussion -> Decision Proposal -> Governance Deliberation -> Voting / Executive Veto -> Approved Decision -> Decision Ledger -> Task DAG Creation.
        """
        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found")

        if meeting.status == MeetingStatus.CONCLUDED:
            raise ValueError(f"Meeting {meeting_id} has already concluded and cannot accept new reports")

        # 1. Propose formal governance proposal
        params = {}
        if tasks:
            params["tasks"] = tasks
        proposal = await self.governance_engine.create_proposal(
            title=topic,
            description=description,
            proposal_type=proposal_type,
            proposer_id=proposer_id,
            project_id=project_id,
            parameters=params,
            auto_discuss=True,
        )

        self._record_activity(
            meeting=meeting,
            speaker_id=proposer_id,
            event_type="governance_proposal_created",
            summary=f"Governance proposal '{proposal.proposal_id}' created on topic '{topic}'",
            data={"proposal_id": proposal.proposal_id, "type": proposal_type.value},
        )

        # 2. Deliberate
        statements = []
        if auto_deliberate:
            proposal, statements = await self.governance_engine.run_deliberation(proposal.proposal_id)
            self._record_activity(
                meeting=meeting,
                speaker_id="BossAI",
                event_type="governance_deliberation_completed",
                summary=f"Boardroom committee completed {len(statements)} deliberation statements on '{proposal.proposal_id}'",
                data={"statements_count": len(statements)},
            )

        # 3. Voting / Executive Veto
        if executive_veto_reason:
            proposal, ledger_entry = await self.governance_engine.veto_proposal(
                proposal_id=proposal.proposal_id,
                reason=executive_veto_reason,
                vetoed_by=proposer_id,
            )
            created_tasks = []
        else:
            if auto_vote:
                await self.governance_engine.open_voting(proposal.proposal_id)
                members = self.governance_engine.committee.get_members(active_only=True)
                for mem in members:
                    await self.governance_engine.cast_vote(
                        proposal_id=proposal.proposal_id,
                        voter_id=mem.member_id,
                        choice=VoteChoice.AYE,
                        rationale=f"Approved during meeting {meeting_id} deliberation.",
                    )
                proposal, ledger_entry = await self.governance_engine.finalize_voting(proposal.proposal_id)
            else:
                proposal, ledger_entry = await self.governance_engine.finalize_voting(
                    proposal.proposal_id, force_decision="APPROVED"
                )

            # 4. Task DAG Creation on approval
            if proposal.status == ProposalStatus.APPROVED:
                proposal, created_tasks = await self.governance_engine.execute_proposal(proposal.proposal_id)
            else:
                created_tasks = []

        # 5. Record decision in meeting minutes
        decision_obj = {
            "decision_id": ledger_entry.entry_id,
            "topic": topic,
            "decision": f"Governance {proposal.status.value}: {description}",
            "rationale": ledger_entry.rationale,
            "decided_by": proposer_id,
            "impact": f"Ratified in Decision Ledger with hash {ledger_entry.entry_hash[:16]}",
            "proposal_id": proposal.proposal_id,
            "ledger_entry_id": ledger_entry.entry_id,
            "ledger_hash": ledger_entry.entry_hash,
            "created_task_ids": [t.task_id for t in created_tasks],
            "timestamp": utc_now().isoformat(),
        }

        meeting.active_speaker_id = proposer_id
        self._record_activity(
            meeting=meeting,
            speaker_id=proposer_id,
            event_type="governance_decision_finalized",
            summary=f"Governance decision recorded in Decision Ledger [{ledger_entry.entry_id}]: {proposal.status.value}",
            data=decision_obj,
        )

        decisions = list(meeting.decisions or [])
        decisions.append(decision_obj)
        meeting.decisions = decisions

        await self.meeting_repo.session.commit()
        await self.meeting_repo.session.refresh(meeting)

        return decision_obj, proposal, ledger_entry, created_tasks

    async def add_action_item(
        self,
        meeting_id: str,
        description: str,
        assigned_to: str,
        deadline: datetime | None = None,
        linked_task_id: str | None = None,
        acceptance_criteria: str | None = None,
    ) -> ActionItem:
        """
        Adds a tracked action item to the meeting, linked to a real Task DAG item.
        Idempotent: Replaying the same action item will not create duplicates.
        """
        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if meeting and meeting.status == MeetingStatus.CONCLUDED:
            existing_items = await self.action_item_repo.list_by_meeting(meeting.id)
            for ex in existing_items:
                if (
                    (linked_task_id and ex.linked_task_id == linked_task_id)
                    or (ex.assigned_to == assigned_to and ActionItemTracker.normalize_desc(ex.description) == ActionItemTracker.normalize_desc(description))
                ):
                    return ex
            raise ValueError(f"Meeting {meeting_id} has already concluded and cannot add new action items.")

        item = await self.action_tracker.create_action_item(
            meeting_id=meeting_id,
            description=description,
            assigned_to=assigned_to,
            deadline=deadline,
            linked_task_id=linked_task_id,
            acceptance_criteria=acceptance_criteria,
        )
        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if meeting:
            # Check if this action item assignment was already recorded (idempotency check)
            existing_log = (meeting.minutes or {}).get("activity_log", [])
            already_logged = any(
                e.get("event_type") == "action_item_assigned"
                and e.get("data", {}).get("action_item_id") == item.id
                for e in existing_log
            )
            if not already_logged:
                self._record_activity(
                    meeting=meeting,
                    speaker_id="BossAI",
                    event_type="action_item_assigned",
                    summary=f"Action item assigned to {assigned_to}: {description}",
                    data={
                        "action_item_id": item.id,
                        "assigned_to": assigned_to,
                        "deadline": deadline.isoformat() if hasattr(deadline, "isoformat") else (str(deadline) if deadline else None),
                        "linked_task_id": linked_task_id,
                        "acceptance_criteria": acceptance_criteria,
                    },
                )
                await self.meeting_repo.session.commit()
                await self.meeting_repo.session.refresh(meeting)
                await self.meeting_repo.session.refresh(item)

        return item

    async def carry_over_unresolved(
        self,
        meeting_id: str,
        from_meeting_id: str | None = None
    ) -> list[ActionItem]:
        """
        Carries forward unresolved action items into this meeting.
        """
        return await self.action_tracker.carry_over_unresolved_items(
            to_meeting_id=meeting_id,
            from_meeting_id=from_meeting_id
        )

    async def conclude_meeting(
        self,
        meeting_id: str,
        summary: str | None = None
    ) -> Meeting:
        """
        Concludes meeting, compiles structured minutes, and records permanent receipt.
        """
        meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
        if not meeting:
            raise ValueError(f"Meeting {meeting_id} not found")

        if meeting.status == MeetingStatus.CONCLUDED:
            # Idempotent replay: meeting already concluded
            return meeting

        now_dt = utc_now()
        action_items = await self.action_tracker.list_for_meeting(meeting.id)
        existing_minutes = dict(meeting.minutes or {})
        progress_reports = existing_minutes.get("progress_reports", [])
        activity_log = list(existing_minutes.get("activity_log", []))
        verified_claims = list(existing_minutes.get("verified_claims", []))

        # Enforce meeting policy controls before concluding
        if "policy_state" in existing_minutes:
            policy_state = dict(existing_minutes["policy_state"])
            can_conclude, violation_reason = self.policy_enforcer.check_can_conclude(
                policy_state=policy_state,
                decisions_count=len(meeting.decisions or []),
            )
            if not can_conclude:
                raise MeetingPolicyViolationError(violation_reason)

        # Record concluded event in activity log
        conclusion_activity = {
            "timestamp": now_dt.isoformat(),
            "speaker_id": "BossAI",
            "event_type": "concluded",
            "summary": "Meeting concluded and minutes stored",
            "data": {},
        }
        activity_log.append(conclusion_activity)

        # Compile structured minutes
        final_minutes = MeetingMinutesGenerator.generate_minutes(
            meeting_id=meeting.meeting_id,
            title=meeting.title,
            meeting_type=meeting.meeting_type,
            participants=meeting.participants,
            agenda=meeting.agenda,
            started_at=meeting.started_at,
            concluded_at=now_dt,
            progress_reports=progress_reports,
            decisions=meeting.decisions,
            action_items=list(action_items),
            summary=summary,
            verified_claims=verified_claims,
        )

        final_minutes["activity_log"] = activity_log
        if "verified_claims" in existing_minutes:
            final_minutes["verified_claims"] = existing_minutes["verified_claims"]
        if "policy_state" in existing_minutes:
            policy_state = dict(existing_minutes["policy_state"])
            policy_state["current_stage"] = "ADJOURN"
            policy_state["concluded_cleanly"] = True
            final_minutes["policy_state"] = policy_state

        meeting.minutes = final_minutes
        meeting.status = MeetingStatus.CONCLUDED
        meeting.concluded_at = now_dt
        meeting.active_speaker_id = None
        meeting.current_stage = "ADJOURN"

        await self.meeting_repo.session.commit()
        await self.meeting_repo.session.refresh(meeting)

        # Notify via Message Bus if connected
        if self.message_bus:
            try:
                await self.message_bus.broadcast(
                    title=f"Boardroom Concluded: {meeting.title}",
                    content=final_minutes.get("summary", "Meeting concluded.")
                )
            except Exception as e:
                logger.warning("Could not broadcast meeting conclusion", error=str(e))

        logger.info("Meeting concluded and minutes stored", meeting_id=meeting_id)
        return meeting

    async def conduct_standup(
        self,
        meeting_id: str | None = None,
        title: str | None = None,
        employee_reports: list[dict[str, Any]] | None = None,
        decisions: list[dict[str, Any]] | None = None,
        action_items: list[dict[str, Any]] | None = None,
        carry_over: bool = True
    ) -> Meeting:
        """
        Orchestrates an end-to-end Daily Standup session:
        Convenes -> Validates Fact Reports -> Carries Over Open Items -> Records Decisions -> Concludes.
        """
        # Resolve or create meeting
        if meeting_id:
            meeting = await self.meeting_repo.get_by_meeting_id(meeting_id)
            if not meeting:
                raise ValueError(f"Meeting {meeting_id} not found")
        else:
            participants = ["BossAI"]
            if self.participant_selector:
                participants = await self.participant_selector.select_participants(MeetingType.DAILY_STANDUP)
            meeting = await self.schedule_meeting(
                title=title or f"Daily Standup — {datetime.now(UTC).strftime('%Y-%m-%d')}",
                meeting_type=MeetingType.DAILY_STANDUP,
                agenda=["1. Progress & Receipts", "2. Blocker Identification", "3. Action Item Allocation"],
                participants=participants
            )

        # Convene
        await self.convene_meeting(meeting.meeting_id)

        # Carry over unresolved items if requested
        if carry_over:
            await self.carry_over_unresolved(meeting.id)

        # Process employee reports with anti-fabrication enforcement
        if employee_reports:
            for rep in employee_reports:
                await self.collect_report(meeting.meeting_id, rep)

        # Record decisions
        if decisions:
            for dec in decisions:
                await self.record_decision(
                    meeting_id=meeting.meeting_id,
                    topic=dec.get("topic", "Standup Decision"),
                    decision=dec.get("decision", ""),
                    rationale=dec.get("rationale", "Consensus during standup"),
                    decided_by=dec.get("decided_by", "BossAI"),
                    impact=dec.get("impact", "Standard")
                )

        # Create action items
        if action_items:
            for ai in action_items:
                await self.add_action_item(
                    meeting_id=meeting.id,
                    description=ai.get("description", ""),
                    assigned_to=ai.get("assigned_to", "BossAI"),
                    deadline=ai.get("deadline")
                )

        # Conclude
        return await self.conclude_meeting(meeting.meeting_id)

    async def conduct_operational_review(
        self,
        task_id: str,
        reporting_employee_id: str,
        what_was_done: str,
        what_remains: str,
        what_is_blocking: str,
        verification_receipt: dict[str, Any],
        decision_topic: str,
        decision_text: str,
        decision_rationale: str,
        action_item_description: str,
        action_item_assigned_to: str,
        action_item_deadline: datetime | None = None,
        project_id: str | None = None,
        **kwargs: Any,
    ) -> Meeting:
        """
        Orchestrates an end-to-end operational boardroom review connecting:
        1. Fact-grounded progress report with verifiable receipts.
        2. Blocker identification and root-cause analysis.
        3. Deliberation and explicit decision with rationale.
        4. Tracked action item with concrete assignee and deadline.
        5. Concludes meeting with comprehensive structured minutes and activity log.
        """
        title = f"Operational Blocker Review: {task_id}"
        agenda = [
            f"1. Factual progress review for {task_id}",
            f"2. Blocker analysis: {what_is_blocking}",
            f"3. Architectural decision on {decision_topic}",
            f"4. Action item assignment to {action_item_assigned_to}",
        ]
        participants = ["BossAI", reporting_employee_id]
        if action_item_assigned_to not in participants:
            participants.append(action_item_assigned_to)

        # 1. Schedule meeting
        meeting = await self.schedule_meeting(
            title=title,
            meeting_type=MeetingType.INCIDENT_REVIEW,
            agenda=agenda,
            participants=participants,
        )

        # 2. Convene meeting
        await self.convene_meeting(meeting.meeting_id)

        # 3. Present fact-grounded report
        report = ProgressReportCompiler.compile(
            employee_id=reporting_employee_id,
            task_id=task_id,
            what_was_done=what_was_done,
            what_remains=what_remains,
            what_is_blocking=what_is_blocking,
            verification_receipt=verification_receipt,
            factual_status="blocked",
        )
        await self.collect_report(meeting.meeting_id, report)

        # 4. Boss AI records remediation decision (optionally routed through Governance Engine)
        as_governance = kwargs.get("as_governance", False)
        dec = await self.record_decision(
            meeting_id=meeting.meeting_id,
            topic=decision_topic,
            decision=decision_text,
            rationale=decision_rationale,
            decided_by="BossAI",
            impact=f"Unblocks {task_id}",
            as_governance=as_governance,
            project_id=project_id,
        )

        # 5. Add tracked action item referencing decision and linked task DAG
        linked_task_id = kwargs.get("action_item_linked_task_id") or kwargs.get("linked_task_id")
        if not linked_task_id and dec.get("created_task_ids"):
            linked_task_id = dec["created_task_ids"][0]
        acceptance_criteria = kwargs.get("action_item_acceptance_criteria") or kwargs.get("acceptance_criteria")
        await self.add_action_item(
            meeting_id=meeting.id,
            description=f"[{dec['decision_id']}] {action_item_description}",
            assigned_to=action_item_assigned_to,
            deadline=action_item_deadline or (utc_now() + timedelta(hours=24)),
            linked_task_id=linked_task_id,
            acceptance_criteria=acceptance_criteria,
        )

        # 6. Conclude meeting
        return await self.conclude_meeting(
            meeting_id=meeting.meeting_id,
            summary=f"Resolved blocker on {task_id} via decision {dec['decision_id']} assigned to {action_item_assigned_to}.",
        )
