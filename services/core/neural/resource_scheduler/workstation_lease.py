from __future__ import annotations

import os
from typing import Any, Optional

from neural.config import get_settings
from neural.db.repositories import ResourceLeaseRepository
from neural.resource_scheduler.hardware_profiler import HardwareProfiler
from neural.resource_scheduler.identity import desktop_slot_resource_key, resolve_resource_pool_id

WORKSTATION_LEASE_SECONDS = 3600


def workstation_slot_limit() -> int:
    try:
        deployment_limit = int(os.environ.get("ANCOS_MAX_ACTIVE_DESKTOPS", "1"))
    except ValueError as exc:
        raise ValueError("ANCOS_MAX_ACTIVE_DESKTOPS must be a non-negative integer.") from exc
    return HardwareProfiler(max_active_desktops=deployment_limit).get_max_desktops()


async def claim_workstation_lease(
    lease_repo: ResourceLeaseRepository,
    employee_id: str,
    docker_id: str,
    resource_pool_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    pool_id = resolve_resource_pool_id(resource_pool_id)
    settings = get_settings()
    for slot_id in range(1, workstation_slot_limit() + 1):
        lease = await lease_repo.claim(
            resource_key=desktop_slot_resource_key(slot_id, pool_id),
            employee_id=employee_id,
            session_id=docker_id,
            lease_seconds=WORKSTATION_LEASE_SECONDS,
            resource_reservation={
                "slot_id": slot_id,
                "resource_pool_id": pool_id,
                "cpu_limit": settings.DEFAULT_CONTAINER_CPU_LIMIT,
                "mem_limit": settings.DEFAULT_CONTAINER_MEM_LIMIT,
                "owner": "approved_employee_workstation_api",
            },
        )
        if lease:
            return lease
    return None


async def renew_workstation_lease(
    lease_repo: ResourceLeaseRepository,
    employee_id: str,
    docker_id: str,
    resource_pool_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    lease = await lease_repo.get_by_session_id(docker_id)
    if lease is None:
        if hasattr(lease_repo, "list_blocking_for_employee"):
            blocking = await lease_repo.list_blocking_for_employee(employee_id)
            active = [b for b in blocking if b.get("status") == "active"]
            if active:
                lease = active[0]
        if lease is None:
            return await claim_workstation_lease(lease_repo, employee_id, docker_id, resource_pool_id)
    if lease["employee_id"] != employee_id or lease["status"] != "active":
        return None
    if not hasattr(lease_repo, "heartbeat"):
        return lease
    expires_at = await lease_repo.heartbeat(
        lease_id=lease["lease_id"],
        fencing_token=lease["fencing_token"],
        lease_seconds=WORKSTATION_LEASE_SECONDS,
    )
    if expires_at is None:
        return None
    lease["lease_expires_at"] = expires_at
    return lease


async def release_workstation_lease(
    lease_repo: ResourceLeaseRepository,
    docker_id: str,
) -> bool:
    lease = await lease_repo.get_by_session_id(docker_id)
    if lease is None:
        return True
    return await lease_repo.release(lease["lease_id"], lease["fencing_token"])
