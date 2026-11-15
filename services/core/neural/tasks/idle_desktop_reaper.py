"""Demand-driven idle-workstation teardown.

The deployment runs with a single desktop slot (``ANCOS_MAX_ACTIVE_DESKTOPS=1``)
and a workstation lease lives for an hour (``WORKSTATION_LEASE_SECONDS``).
Nothing releases an idle desktop when its employee has finished working, so the
slot stays held for the full hour and the *next* employee's task stalls on "no
free workstation slot" -- the request approval exists, but provisioning defers
until the slot frees up. On a busy company that serialises everything behind a
one-hour lock.

This coordinator frees capacity on demand, and only on demand:

* it acts only when every desktop slot is already leased AND some PENDING task
  cannot be dispatched (a real backlog), and
* it terminates only desktops whose employee holds no live claim (no ASSIGNED or
  RUNNING task and not in the WORKING state), preferring the least recently
  started so an in-use desktop is never preferred over an idle one.

Teardown reuses the same primitives as the manual route
(``ContainerManager.terminate_desktop`` plus ``release_workstation_lease``) and
verifies broker-side absence, so an uncertain cleanup keeps the quarantine
rather than granting capacity on a guess.
"""

from __future__ import annotations

import asyncio
import os

from neural.constants import ContainerStatus, EmployeeStatus, TaskStatus
from neural.containers.manager import ContainerManager, DockerBrokerRequestError
from neural.db.engine import AsyncSessionLocal
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    ResourceLeaseRepository,
    TaskRepository,
)
from neural.logger import get_logger
from neural.resource_scheduler.identity import resolve_resource_pool_id
from neural.resource_scheduler.workstation_lease import (
    release_workstation_lease,
    workstation_slot_limit,
)

logger = get_logger("ancos.tasks.idle_desktop_reaper")


def idle_desktop_reap_interval_seconds() -> int:
    raw = os.environ.get("ANCOS_IDLE_DESKTOP_REAP_SECONDS", "30")
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ValueError("ANCOS_IDLE_DESKTOP_REAP_SECONDS must be an integer from 5 to 3600.") from exc
    if not 5 <= interval <= 3600:
        raise ValueError("ANCOS_IDLE_DESKTOP_REAP_SECONDS must be an integer from 5 to 3600.")
    return interval


async def _has_pending_backlog(task_repo: TaskRepository) -> bool:
    return bool(await task_repo.list_by_status(TaskStatus.PENDING))


async def _desktop_slots_exhausted(lease_repo: ResourceLeaseRepository) -> bool:
    """True only when every desktop slot is already leased.

    Reaping frees a slot for a waiting task. Without this guard the reaper
    tears down an idle desktop even while free slots remain, killing a
    workstation that nobody is competing for -- and revoking the VNC token an
    operator is watching. Capacity is exhausted when the count of active
    desktop leases for this pool reaches the configured slot limit.
    """
    limit = workstation_slot_limit()
    if limit <= 0:
        return False
    pool_prefix = f"desktop:{resolve_resource_pool_id()}:"
    active = await lease_repo.list_active()
    leased_slots = {
        lease.get("resource_key")
        for lease in active
        if str(lease.get("resource_key", "")).startswith(pool_prefix)
    }
    return len(leased_slots) >= limit


async def _employee_is_idle(employee_id: str, task_repo: TaskRepository) -> bool:
    tasks = await task_repo.list_by_assignee(employee_id)
    return not any(
        TaskStatus(getattr(task.status, "value", task.status))
        in (TaskStatus.ASSIGNED, TaskStatus.RUNNING)
        for task in tasks
    )


async def reap_idle_desktops(
    task_repo: TaskRepository,
    employee_repo: EmployeeRepository,
    container_repo: ContainerRepository,
    lease_repo: ResourceLeaseRepository,
    container_mgr: ContainerManager,
) -> list[str]:
    """Terminate idle desktops, but only while a PENDING backlog needs the slot."""
    if not await _has_pending_backlog(task_repo):
        return []
    if not await _desktop_slots_exhausted(lease_repo):
        return []

    candidates: list[dict[str, str]] = []
    for lease in await lease_repo.list_active():
        employee_id = str(lease["employee_id"])
        docker_id = str(lease.get("session_id") or f"ancos-desktop-{employee_id.lower()}")
        container = await container_repo.get_by_docker_id(docker_id)
        if container is None or container.status != ContainerStatus.RUNNING:
            continue
        employee = await employee_repo.get_by_employee_id(employee_id)
        if employee is None or employee.status != EmployeeStatus.IDLE:
            continue
        if not await _employee_is_idle(employee_id, task_repo):
            continue
        candidates.append(
            {
                "employee_id": employee_id,
                "docker_id": docker_id,
                "started_at": container.started_at.isoformat() if container.started_at else "",
            }
        )

    # Least recently started first: recycle the oldest idle desktop.
    candidates.sort(key=lambda item: item["started_at"])

    reaped: list[str] = []
    for candidate in candidates:
        employee_id = candidate["employee_id"]
        docker_id = candidate["docker_id"]
        try:
            await container_mgr.terminate_desktop(docker_id, container_repo=container_repo)
            await container_repo.update_status(docker_id, ContainerStatus.STOPPED)
        except DockerBrokerRequestError as exc:
            if exc.status_code != 404:
                logger.warning(
                    "Idle desktop teardown rejected by broker; keeping slot",
                    docker_id=docker_id,
                    error=str(exc),
                )
                continue
        except Exception as exc:
            logger.warning(
                "Idle desktop teardown failed; keeping slot",
                docker_id=docker_id,
                error=str(exc),
            )
            continue

        if await release_workstation_lease(lease_repo, docker_id):
            reaped.append(employee_id)
            logger.info("Reaped idle workstation to free capacity", employee_id=employee_id)
        else:
            logger.warning(
                "Idle desktop stopped but lease release did not confirm; slot stays fenced",
                docker_id=docker_id,
            )
    return reaped


async def idle_desktop_reaper_loop(interval_seconds: int | None = None) -> None:
    interval = interval_seconds or idle_desktop_reap_interval_seconds()
    from neural.api.deps import get_container_manager

    while True:
        try:
            async with AsyncSessionLocal() as session:
                await reap_idle_desktops(
                    TaskRepository(session),
                    EmployeeRepository(session),
                    ContainerRepository(session),
                    ResourceLeaseRepository(session),
                    get_container_manager(),
                )
        except Exception as exc:  # a reaper failure must never crash the app
            logger.error("Idle desktop reaper cycle failed", error=str(exc))
        await asyncio.sleep(interval)
