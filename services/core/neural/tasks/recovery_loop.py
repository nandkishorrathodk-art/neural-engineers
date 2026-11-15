from __future__ import annotations

import asyncio
import os
from typing import Any

from neural.db.engine import AsyncSessionLocal
from neural.db.repositories import ProjectRepository, TaskRepository
from neural.logger import get_logger

logger = get_logger("ancos.tasks.stale_recovery")


def stale_recovery_interval_seconds() -> int:
    raw = os.environ.get("ANCOS_STALE_RECOVERY_SECONDS", "30")
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ValueError("ANCOS_STALE_RECOVERY_SECONDS must be an integer from 5 to 3600.") from exc
    if not 5 <= interval <= 3600:
        raise ValueError("ANCOS_STALE_RECOVERY_SECONDS must be an integer from 5 to 3600.")
    return interval


async def run_stale_recovery_cycle(
    repo: TaskRepository, project_repo: ProjectRepository | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run one recovery pass: requeue stranded tasks, re-dispatch PENDING ones.

    Re-dispatching in the same pass is what prevents a recovered task from
    starving: recovery moves it to PENDING with no owner, and the dispatcher
    immediately re-assigns it to a qualified employee whose desktop is running.

    When a ``ProjectRepository`` is supplied, project lifecycle status is also
    reconciled so a project whose tasks have all completed reaches COMPLETED
    instead of lingering in PLANNING.
    """
    recovered = await repo.recover_stale_assignments()
    redispatched = await repo.redispatch_pending_tasks()
    if project_repo is not None:
        await project_repo.reconcile_statuses()
    return recovered, redispatched


async def stale_assignment_recovery_loop(interval_seconds: int | None = None) -> None:
    """Periodically requeue tasks whose assigned employee can no longer run them.

    Runs independently of the worker fleet so a dead workstation or a capability
    mismatch is recovered even when no worker ever polls the task again. After
    each pass it also re-dispatches PENDING tasks that now have a qualified,
    running desktop, so a requeued task is never stranded waiting for a worker
    that has already stopped polling.
    """
    interval = interval_seconds or stale_recovery_interval_seconds()
    while True:
        try:
            async with AsyncSessionLocal() as session:
                recovered, redispatched = await run_stale_recovery_cycle(
                    TaskRepository(session), ProjectRepository(session)
                )
            if recovered:
                logger.warning(
                    "Recovered stale task assignments",
                    count=len(recovered),
                    tasks=[item["task_id"] for item in recovered],
                )
            if redispatched:
                logger.info(
                    "Re-dispatched recovered PENDING tasks",
                    count=len(redispatched),
                    tasks=redispatched,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Stale assignment recovery cycle failed", error=str(exc))
        await asyncio.sleep(interval)
