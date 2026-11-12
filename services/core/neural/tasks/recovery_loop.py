from __future__ import annotations

import asyncio
import os

from neural.db.engine import AsyncSessionLocal
from neural.db.repositories import TaskRepository
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


async def stale_assignment_recovery_loop(interval_seconds: int | None = None) -> None:
    """Periodically requeue tasks whose assigned employee can no longer run them.

    Runs independently of the worker fleet so a dead workstation or a capability
    mismatch is recovered even when no worker ever polls the task again.
    """
    interval = interval_seconds or stale_recovery_interval_seconds()
    while True:
        try:
            async with AsyncSessionLocal() as session:
                recovered = await TaskRepository(session).recover_stale_assignments()
            if recovered:
                logger.warning(
                    "Recovered stale task assignments",
                    count=len(recovered),
                    tasks=[item["task_id"] for item in recovered],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Stale assignment recovery cycle failed", error=str(exc))
        await asyncio.sleep(interval)
