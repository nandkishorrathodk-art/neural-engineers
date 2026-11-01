import uuid
from datetime import UTC, datetime
from typing import Any

from neural.constants import MeetingStatus, MeetingType
from neural.db.models import ActionItem, Meeting
from neural.db.repositories import ActionItemRepository, MeetingRepository
from neural.logger import get_logger
from neural.meetings.actions import ActionItemTracker
from neural.meetings.minutes import MeetingMinutesGenerator
from neural.meetings.participants import ParticipantSelector
from neural.meetings.reports import (
    FactGroundedProgressReport,
    ProgressReportCompiler,
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
        message_bus: Any | None = None
    ):
        self.meeting_repo = meeting_repo
        self.action_item_repo = action_item_repo or ActionItemRepository(meeting_repo.session)
        self.action_tracker = ActionItemTracker(self.action_item_repo)
        self.participant_selector = participant_selector
        self.message_bus = message_bus

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
            scheduled_at=scheduled_at or utc_now()
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

        meeting.status = MeetingStatus.IN_PROGRESS
        if meeting.started_at is None:
            meeting.started_at = utc_now()
        await self.meeting_repo.session.commit()
        await self.meeting_repo.session.refresh(meeting)

        logger.info("Meeting convened and in progress", meeting_id=meeting_id)
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


        current_minutes = dict(meeting.minutes or {})
        reports_list = list(current_minutes.get("progress_reports", []))
        reports_list.append(validated_dict)
        current_minutes["progress_reports"] = reports_list
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
        impact: str = "Standard workflow"
    ) -> dict[str, Any]:
        """
        Records an explicit decision with its architectural or operational rationale.
        """
        decision_obj = {
            "decision_id": f"DEC-{uuid.uuid4().hex[:6].upper()}",
            "topic": topic,
            "decision": decision,
            "rationale": rationale,
            "decided_by": decided_by,
            "impact": impact,
            "timestamp": utc_now().isoformat()
        }
        await self.meeting_repo.record_decision(meeting_id, decision_obj)
        logger.info(
            "Decision recorded",
            meeting_id=meeting_id,
            decision_id=decision_obj["decision_id"],
            topic=topic
        )
        return decision_obj

    async def add_action_item(
        self,
        meeting_id: str,
        description: str,
        assigned_to: str,
        deadline: datetime | None = None
    ) -> ActionItem:
        """
        Adds a tracked action item to the meeting.
        """
        return await self.action_tracker.create_action_item(
            meeting_id=meeting_id,
            description=description,
            assigned_to=assigned_to,
            deadline=deadline
        )

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

        now_dt = utc_now()
        action_items = await self.action_tracker.list_for_meeting(meeting.id)
        existing_minutes = dict(meeting.minutes or {})
        progress_reports = existing_minutes.get("progress_reports", [])

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
            summary=summary
        )

        meeting.minutes = final_minutes
        meeting.status = MeetingStatus.CONCLUDED
        meeting.concluded_at = now_dt

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
