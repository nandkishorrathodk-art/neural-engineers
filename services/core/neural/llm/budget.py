from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from neural.db.models import Project, Task
from neural.llm.cost import CostTracker


async def load_budget_scope_caps(
    session: AsyncSession,
    cost_tracker: CostTracker,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the durable month/project/task caps for one provider call."""
    task = None
    if task_id:
        task = await session.scalar(select(Task).where(Task.task_id == task_id))
        # Some internal callers use task_id as an observability label before
        # that task is persisted. Those calls still receive the organization
        # month cap; task/project scopes require authoritative DB records.
        if task is not None and project_id and task.project_id != project_id:
            raise ValueError("Task and project budget scopes do not match")
        if task is not None:
            project_id = task.project_id

    project = await session.get(Project, project_id) if project_id else None

    caps: dict[str, Any] = {
        f"organization-month:{datetime.now(timezone.utc):%Y-%m}": cost_tracker.monthly_hard_cap_usd,
    }
    if project is not None:
        project_plan = project.execution_plan if isinstance(project.execution_plan, dict) else {}
        caps[f"project:{project.id}"] = project_plan.get(
            "budget_usd", cost_tracker.max_project_budget_usd
        )
    if task is not None:
        task_budget = task.resource_budget if isinstance(task.resource_budget, dict) else {}
        caps[f"task:{task.task_id}"] = task_budget.get(
            "max_cost_usd", cost_tracker.max_task_budget_usd
        )
    return caps
