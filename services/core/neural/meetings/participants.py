from typing import Any

from neural.constants import EmployeeStatus, MeetingType
from neural.db.repositories import EmployeeRepository, TaskRepository
from neural.logger import get_logger

logger = get_logger("ancos.meetings.participants")


class ParticipantSelector:
    """
    Dynamic Participant Selector for Autonomous Meetings.
    Selects attendees based on meeting type, active tasks, project scope, and department roles.
    """

    def __init__(
        self,
        employee_repo: EmployeeRepository,
        task_repo: TaskRepository | None = None
    ):
        self.employee_repo = employee_repo
        self.task_repo = task_repo

    async def select_participants(
        self,
        meeting_type: MeetingType,
        project_id: str | None = None,
        task_ids: list[str] | None = None,
        department: str | None = None,
        include_boss: bool = True
    ) -> list[str]:
        """
        Dynamically selects participant employee IDs (and BossAI) based on the meeting type and context.
        """
        participants: list[str] = []
        if include_boss:
            participants.append("BossAI")

        all_employees = await self.employee_repo.list_all()
        # Filter out terminated employees
        active_employees = [
            e for e in all_employees
            if e.status != EmployeeStatus.TERMINATED
        ]

        if meeting_type == MeetingType.EXECUTIVE_BRIEFING:
            participants.append("HumanOwner")
            # Include department leads/seniors if available
            for emp in active_employees:
                if any(k in emp.title.lower() for k in ["lead", "principal", "architect", "senior"]) and emp.employee_id not in participants:
                    participants.append(emp.employee_id)
            return participants

        if meeting_type == MeetingType.INCIDENT_REVIEW:
            # Attendees: Assignees of the blocked/failed tasks, QA engineers, and relevant specialists
            task_assignee_ids: set[str] = set()
            if self.task_repo and task_ids:
                for tid in task_ids:
                    task = await self.task_repo.get_by_task_id(tid)
                    if task and task.assigned_to:
                        # task.assigned_to might be Employee.id UUID or employee_id
                        for emp in active_employees:
                            if emp.id == task.assigned_to or emp.employee_id == task.assigned_to:
                                task_assignee_ids.add(emp.employee_id)

            for emp in active_employees:
                is_qa = "qa" in emp.department.lower() or "qa" in emp.title.lower() or "quality" in emp.title.lower()
                is_target_assignee = emp.employee_id in task_assignee_ids
                if (is_target_assignee or is_qa) and emp.employee_id not in participants:
                    participants.append(emp.employee_id)

            # If no specific assignees found, include all active engineers
            if len(participants) <= 1:
                for emp in active_employees:
                    if emp.employee_id not in participants:
                        participants.append(emp.employee_id)
            return participants

        if meeting_type == MeetingType.PROJECT_REVIEW and project_id and self.task_repo:
            tasks = await self.task_repo.list_by_project(project_id)
            project_emp_uuids = {t.assigned_to for t in tasks if t.assigned_to}
            for emp in active_employees:
                if (emp.id in project_emp_uuids or emp.employee_id in project_emp_uuids) and emp.employee_id not in participants:
                    participants.append(emp.employee_id)

            # Ensure at least QA is included
            for emp in active_employees:
                if ("qa" in emp.title.lower() or "quality" in emp.title.lower()) and emp.employee_id not in participants:
                    participants.append(emp.employee_id)

            if len(participants) <= 1:
                for emp in active_employees:
                    if emp.employee_id not in participants:
                        participants.append(emp.employee_id)
            return participants

        if meeting_type == MeetingType.ARCHITECTURE_REVIEW:
            for emp in active_employees:
                title_lower = emp.title.lower()
                if any(role in title_lower for role in ["architect", "senior", "lead", "principal", "backend"]) and emp.employee_id not in participants:
                    participants.append(emp.employee_id)
            if len(participants) <= 1:
                for emp in active_employees:
                    if emp.employee_id not in participants:
                        participants.append(emp.employee_id)
            return participants


        # Default / DAILY_STANDUP / AD_HOC:
        # Include all non-terminated employees (or filtered by department if specified)
        for emp in active_employees:
            if department and emp.department.lower() != department.lower():
                continue
            if emp.employee_id not in participants:
                participants.append(emp.employee_id)

        logger.info(
            "Selected participants for meeting",
            meeting_type=meeting_type.value,
            count=len(participants),
            participants=participants
        )
        return participants

    async def get_participant_profiles(self, participant_ids: list[str]) -> list[dict[str, Any]]:
        """
        Retrieves profile metadata for each participant ID.
        """
        profiles: list[dict[str, Any]] = []
        all_employees = await self.employee_repo.list_all()
        emp_map = {e.employee_id: e for e in all_employees}

        for pid in participant_ids:
            if pid == "BossAI":
                profiles.append({
                    "id": "BossAI",
                    "employee_id": "BossAI",
                    "name": "Central Brain (CEO)",
                    "title": "Chief Executive Officer & Chief Architect",
                    "department": "Executive",
                    "status": "operational"
                })
            elif pid == "HumanOwner":
                profiles.append({
                    "id": "HumanOwner",
                    "employee_id": "HumanOwner",
                    "name": "Executive Board / Human Owner",
                    "title": "Sole Proprietor & Executive Director",
                    "department": "Board of Directors",
                    "status": "sovereign"
                })
            elif pid in emp_map:
                emp = emp_map[pid]
                profiles.append({
                    "id": emp.id,
                    "employee_id": emp.employee_id,
                    "name": emp.name,
                    "title": emp.title,
                    "department": emp.department,
                    "status": emp.status.value if hasattr(emp.status, "value") else str(emp.status)
                })
            else:
                profiles.append({
                    "id": pid,
                    "employee_id": pid,
                    "name": pid,
                    "title": "Collaborator",
                    "department": "Engineering",
                    "status": "active"
                })
        return profiles
