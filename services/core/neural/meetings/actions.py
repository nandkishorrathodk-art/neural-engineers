from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from neural.constants import ActionItemStatus
from neural.db.models import ActionItem
from neural.db.repositories import ActionItemRepository, action_item_transition_allowed
from neural.exceptions import ActionItemConflictError
from neural.logger import get_logger

logger = get_logger("ancos.meetings.actions")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ActionItemTracker:
    """
    Action Item Tracker for Boardroom & Autonomous Meetings.
    Manages action item lifecycles, deadline tracking, and automatic carry-over
    of unresolved tasks across subsequent meetings.
    """

    def __init__(self, action_item_repo: ActionItemRepository):
        self.repo = action_item_repo

    async def create_action_item(
        self,
        meeting_id: str,
        description: str,
        assigned_to: str,
        deadline: datetime | None = None,
        status: ActionItemStatus = ActionItemStatus.OPEN,
        linked_task_id: str | None = None,
        acceptance_criteria: str | None = None,
    ) -> ActionItem:
        """
        Creates and persists a new action item associated with a meeting.
        """
        item = await self.repo.create(
            meeting_id=meeting_id,
            description=description,
            assigned_to=assigned_to,
            deadline=deadline,
            status=status,
            linked_task_id=linked_task_id,
            acceptance_criteria=acceptance_criteria,
        )
        logger.info(
            "Created action item",
            action_item_id=item.id,
            meeting_id=meeting_id,
            assigned_to=assigned_to,
            linked_task_id=linked_task_id,
        )
        return item

    @staticmethod
    def normalize_desc(d: str) -> str:
        cleaned = d or ""
        while cleaned.startswith("[Carried Over]"):
            cleaned = cleaned[len("[Carried Over]"):].strip()
        return cleaned.strip()

    async def update_status(self, action_item_id: str, status: ActionItemStatus) -> ActionItem | None:
        """
        Updates status without reopening terminal action items or skipping work state.
        If resolved or cancelled, synchronizes unresolved linked carried-over instances.
        """
        current = await self.repo.get_by_id(action_item_id)
        if not current:
            return None
        if not action_item_transition_allowed(current.status, status):
            raise ActionItemConflictError(
                f"Action item cannot transition from '{current.status.value}' to '{status.value}'."
            )

        item = await self.repo.update_status(action_item_id, status)
        if item:
            logger.info("Updated action item status", action_item_id=action_item_id, status=status.value)
            if status in (ActionItemStatus.RESOLVED, ActionItemStatus.CANCELLED):
                # Synchronize all linked historical/carried-over instances
                norm_desc = self.normalize_desc(item.description)
                all_items = await self.repo.list_all()
                for other in all_items:
                    if other.id != item.id and other.assigned_to == item.assigned_to:
                        if (
                            self.normalize_desc(other.description) == norm_desc
                            and other.status in (ActionItemStatus.OPEN, ActionItemStatus.IN_PROGRESS)
                        ):
                            await self.repo.update_status(other.id, status)
        return item

    async def resolve_action_item(self, action_item_id: str) -> ActionItem | None:
        """
        Marks an action item as resolved, and resolves all linked carried-over instances.
        """
        return await self.update_status(action_item_id, ActionItemStatus.RESOLVED)

    async def reopen_action_item(
        self,
        action_item_id: str,
        reason: str,
        reopened_by: str = "BossAI"
    ) -> ActionItem:
        """
        Formally reopens a resolved or cancelled action item back to OPEN.
        """
        item = await self.repo.reopen_item(action_item_id=action_item_id, reason=reason, reopened_by=reopened_by)
        logger.info(
            "Action item formally reopened",
            action_item_id=action_item_id,
            reason=reason,
            reopened_by=reopened_by
        )
        return item

    async def list_for_meeting(self, meeting_id: str) -> Sequence[ActionItem]:
        return await self.repo.list_by_meeting(meeting_id)

    async def list_unresolved(self) -> Sequence[ActionItem]:
        return await self.repo.list_unresolved()

    async def list_overdue(self) -> Sequence[ActionItem]:
        return await self.repo.list_overdue()

    async def carry_over_unresolved_items(
        self,
        to_meeting_id: str,
        from_meeting_id: str | None = None
    ) -> list[ActionItem]:
        """
        Carries forward unresolved action items into a new meeting.
        Ensures that open deliverables from past standups never slip through the cracks.
        """
        if from_meeting_id:
            all_source_items = await self.repo.list_by_meeting(from_meeting_id)
            unresolved = [
                i for i in all_source_items
                if i.status in (ActionItemStatus.OPEN, ActionItemStatus.IN_PROGRESS)
            ]
        else:
            unresolved = list(await self.repo.list_unresolved())

        existing_in_target = await self.repo.list_by_meeting(to_meeting_id)
        existing_descriptions = {self.normalize_desc(i.description) for i in existing_in_target}
        existing_tasks = {i.linked_task_id for i in existing_in_target if getattr(i, "linked_task_id", None)}

        carried_over: list[ActionItem] = []
        for item in unresolved:
            norm = self.normalize_desc(item.description)
            # Prevent self-carry or duplicate carry into same meeting
            if item.meeting_id == to_meeting_id or norm in existing_descriptions:
                continue
            if item.linked_task_id and item.linked_task_id in existing_tasks:
                continue

            desc = f"[Carried Over] {norm}"

            new_item = await self.repo.create(
                meeting_id=to_meeting_id,
                description=desc,
                assigned_to=item.assigned_to,
                deadline=item.deadline,
                status=item.status,
                linked_task_id=item.linked_task_id,
                acceptance_criteria=item.acceptance_criteria,
            )
            carried_over.append(new_item)
            existing_descriptions.add(norm)
            if item.linked_task_id:
                existing_tasks.add(item.linked_task_id)

        logger.info(
            "Carried over unresolved action items",
            to_meeting_id=to_meeting_id,
            count=len(carried_over)
        )
        return carried_over

    @staticmethod
    def is_overdue(item: ActionItem) -> bool:
        if not item.deadline or item.status in (ActionItemStatus.RESOLVED, ActionItemStatus.CANCELLED):
            return False
        dl = item.deadline
        if isinstance(dl, str):
            try:
                dt = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                dl = dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
            except Exception:
                return False
        elif hasattr(dl, "tzinfo") and dl.tzinfo is not None:
            dl = dl.astimezone(UTC).replace(tzinfo=None)
        return dl < utc_now()

    @staticmethod
    def serialize_action_item(item: ActionItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "meeting_id": item.meeting_id,
            "description": item.description,
            "assigned_to": item.assigned_to,
            "deadline": item.deadline.isoformat() if hasattr(item.deadline, "isoformat") else (str(item.deadline) if item.deadline else None),
            "status": item.status.value if hasattr(item.status, "value") else str(item.status),
            "created_at": item.created_at.isoformat() if hasattr(item.created_at, "isoformat") else (str(item.created_at) if item.created_at else None),
            "is_overdue": ActionItemTracker.is_overdue(item),
            "linked_task_id": getattr(item, "linked_task_id", None),
            "acceptance_criteria": getattr(item, "acceptance_criteria", None),
        }
