from __future__ import annotations

import asyncio
import os

from neural.containers.manager import ContainerManager, DockerBrokerRequestError
from neural.db.engine import AsyncSessionLocal
from neural.db.repositories import ResourceLeaseRepository
from neural.logger import get_logger
from neural.resource_scheduler.identity import resolve_resource_pool_id

logger = get_logger("ancos.resource_scheduler.lease_reaper")


async def reconcile_quarantined_desktop_leases(
    lease_repo: ResourceLeaseRepository,
    container_manager: ContainerManager,
    resource_pool_id: str | None = None,
) -> int:
    """Remove expired managed desktops before releasing their resource fences."""
    pool_id = resolve_resource_pool_id(resource_pool_id)
    prefix = f"desktop:{pool_id}:"
    recovered = 0

    for lease in await lease_repo.list_expired():
        if not str(lease["resource_key"]).startswith(prefix):
            continue
        employee_id = str(lease["employee_id"])
        docker_id = f"ancos-desktop-{employee_id.lower()}"

        try:
            try:
                await container_manager._broker_request("GET", f"/v1/containers/{docker_id}")
            except DockerBrokerRequestError as exc:
                if exc.status_code != 404:
                    raise
            else:
                try:
                    await container_manager._broker_request(
                        "POST", f"/v1/containers/{docker_id}/terminate"
                    )
                except DockerBrokerRequestError as exc:
                    if exc.status_code != 404:
                        raise
                # A successful terminate response is not the release receipt:
                # confirm broker inspection now reports the resource absent.
                try:
                    await container_manager._broker_request("GET", f"/v1/containers/{docker_id}")
                except DockerBrokerRequestError as exc:
                    if exc.status_code != 404:
                        raise
                else:
                    logger.warning(
                        "Expired desktop lease remains quarantined; broker still reports workstation",
                        docker_id=docker_id,
                    )
                    continue

            released = await lease_repo.release(lease["lease_id"], lease["fencing_token"])
            if released:
                recovered += 1
                logger.info(
                    "Released quarantined desktop lease after broker confirmed workstation absence",
                    docker_id=docker_id,
                    fencing_token=lease["fencing_token"],
                )
        except Exception as exc:
            # Broker unavailability, malformed replies, and DB errors retain the
            # quarantine. Capacity is never granted on an uncertain cleanup.
            logger.warning(
                "Expired desktop lease cleanup could not be verified; retaining quarantine",
                docker_id=docker_id,
                error=str(exc),
            )
    return recovered


def lease_reaper_interval_seconds() -> int:
    raw = os.environ.get("ANCOS_RESOURCE_LEASE_REAPER_SECONDS", "30")
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ValueError("ANCOS_RESOURCE_LEASE_REAPER_SECONDS must be an integer from 5 to 3600.") from exc
    if not 5 <= interval <= 3600:
        raise ValueError("ANCOS_RESOURCE_LEASE_REAPER_SECONDS must be an integer from 5 to 3600.")
    return interval


async def resource_lease_reaper_loop(
    container_manager: ContainerManager,
    interval_seconds: int | None = None,
    resource_pool_id: str | None = None,
) -> None:
    interval = interval_seconds or lease_reaper_interval_seconds()
    while True:
        try:
            async with AsyncSessionLocal() as session:
                await reconcile_quarantined_desktop_leases(
                    ResourceLeaseRepository(session),
                    container_manager,
                    resource_pool_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Resource lease reaper cycle failed; leases remain fenced", error=str(exc))
        await asyncio.sleep(interval)
