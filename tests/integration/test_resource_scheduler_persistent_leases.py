from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from neural.constants import TaskPriority
from neural.containers.manager import DockerBrokerRequestError
from neural.db.models import ResourceLease
from neural.db.repositories import ResourceLeaseRepository
from neural.resource_scheduler.checkpoint_manager import CheckpointManager
from neural.resource_scheduler.hardware_profiler import PROFILE_16GB, HardwareProfiler
from neural.resource_scheduler.lease_reaper import reconcile_quarantined_desktop_leases
from neural.resource_scheduler.resource_monitor import ResourceMonitor
from neural.resource_scheduler.scheduler import DesktopResourceScheduler
from sqlalchemy import select


class FakeContainerManager:
    def __init__(self, *, provision_error: Exception | None = None, terminate_result: bool = True):
        self.provision_error = provision_error
        self.terminate_result = terminate_result

    async def provision_employee_desktop(
        self,
        employee_id: str,
        role_image: str = "employee-base",
        cpu_limit: float | None = None,
        mem_limit: str | None = None,
    ):
        self.last_limits = (cpu_limit, mem_limit)
        if self.provision_error:
            raise self.provision_error
        return {
            "docker_id": f"ancos-desktop-{employee_id.lower()}",
            "workspace": f"/tmp/workspaces/{employee_id}",
            "desktop_url": "http://localhost:6081/vnc.html?autoconnect=true",
        }

    async def terminate_desktop(self, _docker_id: str) -> bool:
        return self.terminate_result


class FakeBrokerManager:
    def __init__(self, *, remains_after_terminate: bool = False, unavailable: bool = False):
        self.present = True
        self.remains_after_terminate = remains_after_terminate
        self.unavailable = unavailable

    async def _broker_request(self, method: str, path: str, **_kwargs):
        if self.unavailable:
            raise RuntimeError("broker unavailable")
        if method == "GET":
            if not self.present:
                raise DockerBrokerRequestError("not found", 404)
            return {"status": "running"}
        if method == "POST" and path.endswith("/terminate"):
            if not self.remains_after_terminate:
                self.present = False
            return {"status": "terminated"}
        raise AssertionError(f"Unexpected broker request {method} {path}")


def make_scheduler(repo, container_manager, tmp_path):
    monitor = ResourceMonitor()
    monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
    from neural.resource_scheduler.desktop_pool import DesktopSlotPool

    return DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB, max_active_desktops=1),
        monitor=monitor,
        slot_pool=DesktopSlotPool(max_slots=1, container_manager=container_manager),
        checkpoint_manager=CheckpointManager(str(tmp_path)),
        resource_lease_repo=repo,
        resource_lease_seconds=30,
        resource_pool_id="test-host",
    )


@pytest.mark.asyncio
async def test_durable_resource_lease_fences_competing_scheduler_and_releases_for_queue(
    async_session, tmp_path
):
    repository = ResourceLeaseRepository(async_session)
    first_container_manager = FakeContainerManager()
    first = make_scheduler(repository, first_container_manager, tmp_path / "first")
    second = make_scheduler(repository, FakeContainerManager(), tmp_path / "second")

    first_grant = await first.request_desktop_session("ANC-EMP-1", "TASK-1")
    blocked = await second.request_desktop_session(
        "ANC-EMP-2", "TASK-2", priority=TaskPriority.HIGH
    )

    assert first_grant["status"] == "active"
    assert first_grant["resource_lease"]["fencing_token"] == 1
    assert first_grant["resource_lease"]["resource_key"] == "desktop:test-host:slot-1"
    assert first_container_manager.last_limits == (1.5, "2g")
    assert blocked["status"] == "queued_standby"
    assert "durable resource leases" in blocked["reason"]

    renewed = await first.request_desktop_session("ANC-EMP-1", "TASK-1")
    assert renewed["resource_lease"]["lease_id"] == first_grant["resource_lease"]["lease_id"]
    assert renewed["resource_lease"]["lease_expires_at"] >= first_grant["resource_lease"]["lease_expires_at"]

    await first.suspend_and_release("ANC-EMP-1", "TASK-1", dispatch_next=False)
    dispatched = await second.dispatch_queued_employees()
    assert len(dispatched) == 1
    assert dispatched[0]["status"] == "active"
    assert dispatched[0]["resource_lease"]["fencing_token"] == 2

    leases = (await async_session.execute(select(ResourceLease).order_by(ResourceLease.fencing_token))).scalars().all()
    assert [(lease.fencing_token, lease.status) for lease in leases] == [(1, "released"), (2, "active")]


@pytest.mark.asyncio
async def test_provision_failure_releases_claimed_durable_lease(async_session, tmp_path):
    repository = ResourceLeaseRepository(async_session)
    scheduler = make_scheduler(
        repository,
        FakeContainerManager(provision_error=RuntimeError("broker unavailable")),
        tmp_path,
    )

    with pytest.raises(RuntimeError, match="broker unavailable"):
        await scheduler.request_desktop_session("ANC-EMP-FAIL", "TASK-FAIL")

    lease = await async_session.scalar(select(ResourceLease))
    assert lease is not None
    assert lease.status == "released"
    assert scheduler.get_stats()["active_desktops_count"] == 0


@pytest.mark.asyncio
async def test_failed_container_termination_retains_slot_and_durable_lease(async_session, tmp_path):
    repository = ResourceLeaseRepository(async_session)
    scheduler = make_scheduler(
        repository,
        FakeContainerManager(terminate_result=False),
        tmp_path,
    )
    await scheduler.request_desktop_session("ANC-EMP-KEEP", "TASK-KEEP")

    with pytest.raises(RuntimeError, match="could not be safely released"):
        await scheduler.suspend_and_release("ANC-EMP-KEEP", "TASK-KEEP")

    lease = await async_session.scalar(select(ResourceLease))
    assert lease is not None
    assert lease.status == "active"
    assert scheduler.get_stats()["active_employees"] == ["ANC-EMP-KEEP"]
    assert scheduler.get_stats()["active_desktops_count"] == 1


@pytest.mark.asyncio
async def test_expired_lease_stays_quarantined_until_explicit_cleanup_release(async_session):
    repository = ResourceLeaseRepository(async_session)
    original = await repository.claim(
        resource_key="desktop:test-host:slot-1",
        employee_id="ANC-EMP-STALE",
        lease_seconds=5,
    )
    assert original is not None

    row = await async_session.get(ResourceLease, original["lease_id"])
    assert row is not None
    row.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    await async_session.commit()

    # TTL expiry revokes the old heartbeat, but cannot prove the container exited.
    assert await repository.claim(
        resource_key="desktop:test-host:slot-1",
        employee_id="ANC-EMP-NEW",
        lease_seconds=30,
    ) is None
    quarantined = await repository.list_expired()
    assert [lease["lease_id"] for lease in quarantined] == [original["lease_id"]]
    assert await repository.heartbeat(original["lease_id"], original["fencing_token"]) is None

    # Reuse is allowed only after the cleanup owner confirms the resource is gone.
    assert await repository.release(original["lease_id"], original["fencing_token"])
    replacement = await repository.claim(
        resource_key="desktop:test-host:slot-1",
        employee_id="ANC-EMP-NEW",
        lease_seconds=30,
    )
    assert replacement is not None
    assert replacement["fencing_token"] == original["fencing_token"] + 1


@pytest.mark.asyncio
async def test_scheduler_terminates_its_expired_session_before_releasing_quarantine(
    async_session, tmp_path
):
    repository = ResourceLeaseRepository(async_session)
    scheduler = make_scheduler(repository, FakeContainerManager(), tmp_path / "stale")
    grant = await scheduler.request_desktop_session("ANC-EMP-STALE", "TASK-STALE")
    row = await async_session.get(ResourceLease, grant["resource_lease"]["lease_id"])
    assert row is not None
    row.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    await async_session.commit()

    reclaimed = await scheduler.reconcile_expired_leases()

    assert reclaimed == [grant["slot_id"]]
    assert scheduler.get_stats()["active_desktops_count"] == 0
    row = await async_session.get(ResourceLease, grant["resource_lease"]["lease_id"])
    assert row is not None
    assert row.status == "released"


async def expired_lease(repository, employee_id: str, resource_key: str):
    lease = await repository.claim(
        resource_key=resource_key,
        employee_id=employee_id,
        session_id=f"desktop-slot-1:{employee_id}",
        lease_seconds=5,
    )
    assert lease is not None
    row = await repository.session.get(ResourceLease, lease["lease_id"])
    assert row is not None
    row.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    await repository.session.commit()
    return lease


@pytest.mark.asyncio
async def test_lease_reaper_verifies_broker_absence_before_releasing(async_session):
    repository = ResourceLeaseRepository(async_session)
    lease = await expired_lease(repository, "ANC-EMP-REAP", "desktop:test-host:slot-1")

    released_count = await reconcile_quarantined_desktop_leases(
        repository, FakeBrokerManager(), resource_pool_id="test-host"
    )

    row = await async_session.get(ResourceLease, lease["lease_id"])
    assert released_count == 1
    assert row is not None
    assert row.status == "released"


@pytest.mark.asyncio
async def test_lease_reaper_retains_quarantine_when_broker_still_sees_container(async_session):
    repository = ResourceLeaseRepository(async_session)
    lease = await expired_lease(repository, "ANC-EMP-REAP-LIVE", "desktop:test-host:slot-1")

    released_count = await reconcile_quarantined_desktop_leases(
        repository,
        FakeBrokerManager(remains_after_terminate=True),
        resource_pool_id="test-host",
    )

    row = await async_session.get(ResourceLease, lease["lease_id"])
    assert released_count == 0
    assert row is not None
    assert row.status == "expired"


@pytest.mark.asyncio
async def test_lease_reaper_keeps_other_pool_and_broker_errors_quarantined(async_session):
    repository = ResourceLeaseRepository(async_session)
    other_pool = await expired_lease(repository, "ANC-EMP-OTHER-POOL", "desktop:other-host:slot-1")
    unavailable = await expired_lease(repository, "ANC-EMP-REAP-ERR", "desktop:test-host:slot-1")

    released_count = await reconcile_quarantined_desktop_leases(
        repository,
        FakeBrokerManager(unavailable=True),
        resource_pool_id="test-host",
    )

    other_row = await async_session.get(ResourceLease, other_pool["lease_id"])
    unavailable_row = await async_session.get(ResourceLease, unavailable["lease_id"])
    assert released_count == 0
    assert other_row is not None and other_row.status == "expired"
    assert unavailable_row is not None and unavailable_row.status == "expired"
