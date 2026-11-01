from datetime import UTC, datetime
from typing import Any

from neural.constants import MeetingType
from neural.db.models import Meeting
from neural.db.repositories import MeetingRepository, ProjectRepository, TaskRepository
from neural.logger import get_logger
from neural.meetings.conductor import MeetingConductor
from neural.meetings.participants import ParticipantSelector

logger = get_logger("ancos.meetings.scheduler")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MeetingScheduler:
    """
    Meeting Scheduler for ANCOS Autonomous Corporate Governance.
    Coordinates scheduled triggers (daily standups) and event-based triggers
    (task blockers, phase transitions, critical incidents) with dynamic participant selection.
    """

    def __init__(
        self,
        meeting_repo: MeetingRepository,
        conductor: MeetingConductor,
        participant_selector: ParticipantSelector,
        task_repo: TaskRepository | None = None,
        project_repo: ProjectRepository | None = None,
        message_bus: Any | None = None
    ):
        self.meeting_repo = meeting_repo
        self.conductor = conductor
        self.participant_selector = participant_selector
        self.task_repo = task_repo
        self.project_repo = project_repo
        self.message_bus = message_bus

    async def trigger_daily_standup(
        self,
        project_id: str | None = None,
        department: str | None = None
    ) -> Meeting:
        """
        Scheduled trigger: Schedules or convenes the daily standup across active employees.
        """
        participants = await self.participant_selector.select_participants(
            meeting_type=MeetingType.DAILY_STANDUP,
            project_id=project_id,
            department=department
        )

        title = f"Daily Engineering Standup — {datetime.now(UTC).strftime('%Y-%m-%d')}"
        agenda = [
            "1. What was done? (Empirical receipts)",
            "2. What remains?",
            "3. What is blocking?",
            "4. Verification receipts and exit codes",
            "5. Unresolved action items carry-over"
        ]

        meeting = await self.conductor.schedule_meeting(
            title=title,
            meeting_type=MeetingType.DAILY_STANDUP,
            agenda=agenda,
            participants=participants
        )

        logger.info(
            "Daily standup triggered",
            meeting_id=meeting.meeting_id,
            participants_count=len(participants)
        )
        return meeting

    async def on_task_blocked(
        self,
        task_id: str,
        blocker_reason: str,
        project_id: str | None = None
    ) -> Meeting:
        """
        Event-based trigger: Automatically convenes an emergency incident/blocker sync
        when a task encounters a critical blocker.
        """
        logger.warning(
            "Task blocked event triggered boardroom sync",
            task_id=task_id,
            reason=blocker_reason
        )

        if not project_id and self.task_repo:
            task = await self.task_repo.get_by_task_id(task_id)
            if task:
                project_id = task.project_id

        participants = await self.participant_selector.select_participants(
            meeting_type=MeetingType.INCIDENT_REVIEW,
            project_id=project_id,
            task_ids=[task_id]
        )

        title = f"Urgent Blocker Review: Task `{task_id}`"
        agenda = [
            f"1. Root cause analysis for blocker on task {task_id}",
            f"2. Reported issue: {blocker_reason}",
            "3. Technical remediation & unblocking plan",
            "4. Immediate action item assignment"
        ]

        meeting = await self.conductor.schedule_meeting(
            title=title,
            meeting_type=MeetingType.INCIDENT_REVIEW,
            agenda=agenda,
            participants=participants
        )

        # Notify via Message Bus if available
        if self.message_bus:
            try:
                await self.message_bus.broadcast(
                    title=f"Emergency Boardroom Convened: Blocker on {task_id}",
                    content=f"Boss AI convened an Incident Review meeting ({meeting.meeting_id}) for blocker: {blocker_reason}"
                )
            except Exception as e:
                logger.warning("Could not broadcast task blocked event", error=str(e))

        return meeting

    async def on_phase_completed(
        self,
        project_id: str,
        phase_id: str,
        completed_tasks: list[str] | None = None
    ) -> Meeting:
        """
        Event-based trigger: Convenes a Project Review milestone meeting
        when an entire phase of a project finishes executing.
        """
        logger.info(
            "Phase completion event triggered milestone meeting",
            project_id=project_id,
            phase_id=phase_id
        )

        if completed_tasks is None and self.task_repo:
            tasks = await self.task_repo.list_by_project(project_id)
            completed_tasks = [t.task_id for t in tasks if getattr(t.status, "value", str(t.status)) == "completed"]

        participants = await self.participant_selector.select_participants(
            meeting_type=MeetingType.PROJECT_REVIEW,
            project_id=project_id,
            task_ids=completed_tasks
        )

        title = f"Milestone Review: {phase_id} Completion"
        agenda = [
            f"1. Audit phase {phase_id} deliverables and verification receipts",
            "2. Review QA test results and performance metrics",
            "3. Ratify architecture decisions",
            "4. Authorize progression to next project phase"
        ]

        meeting = await self.conductor.schedule_meeting(
            title=title,
            meeting_type=MeetingType.PROJECT_REVIEW,
            agenda=agenda,
            participants=participants
        )

        if self.message_bus:
            try:
                await self.message_bus.broadcast(
                    title=f"Milestone Review Scheduled: {phase_id}",
                    content=f"Phase {phase_id} completed. Project Review convened ({meeting.meeting_id})."
                )
            except Exception as e:
                logger.warning("Could not broadcast phase completion event", error=str(e))

        return meeting

    async def on_critical_incident(
        self,
        incident_title: str,
        details: str,
        affected_tasks: list[str] | None = None
    ) -> Meeting:
        """
        Event-based trigger for high-severity organizational or technical incidents.
        """
        participants = await self.participant_selector.select_participants(
            meeting_type=MeetingType.INCIDENT_REVIEW,
            task_ids=affected_tasks
        )

        meeting = await self.conductor.schedule_meeting(
            title=f"Post-Mortem: {incident_title}",
            meeting_type=MeetingType.INCIDENT_REVIEW,
            agenda=[
                f"1. Incident overview: {details}",
                "2. Impact assessment across active tasks",
                "3. Preventive policy modifications and action items"
            ],
            participants=participants
        )
        return meeting
