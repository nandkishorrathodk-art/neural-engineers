from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest
from neural.constants import TaskPriority
from neural.containers.manager import ContainerManager
from neural.resource_scheduler.checkpoint_manager import CheckpointManager
from neural.resource_scheduler.desktop_pool import DesktopSlotPool
from neural.resource_scheduler.employee_queue import EmployeeExecutionQueue
from neural.resource_scheduler.hardware_profiler import (
    PROFILE_16GB,
    PROFILE_64GB,
    HardwareProfiler,
)
from neural.resource_scheduler.resource_monitor import ResourceMonitor
from neural.resource_scheduler.scheduler import DesktopResourceScheduler
from neural.resource_scheduler.session_lease import SessionLeaseManager


@pytest.fixture(autouse=True)
def fake_container_manager(monkeypatch):
    async def provision(_self, employee_id: str, role_image: str = "employee-base", **_limits):
        return {
            "docker_id": f"ancos-desktop-{employee_id.lower()}",
            "workspace": f"./data/workspaces/{employee_id}",
            "desktop_url": "http://localhost:6081/vnc.html?autoconnect=true",
        }

    async def terminate(_self, _docker_id: str):
        return True

    monkeypatch.setattr(ContainerManager, "provision_employee_desktop", provision)
    monkeypatch.setattr(ContainerManager, "terminate_desktop", terminate)


def test_hardware_profiler_and_scaling_profiles(monkeypatch):
    profiler = HardwareProfiler(profile=PROFILE_16GB)

    # Initial 16GB profile
    assert profiler.get_max_desktops() == 4
    assert profiler.get_headroom_gb() == 1.5
    assert profiler.active_profile.total_ram_gb == 16.0

    limited_profiler = HardwareProfiler(profile=PROFILE_16GB, max_active_desktops=1)
    assert limited_profiler.get_max_desktops() == 1
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "2")
    assert HardwareProfiler(profile=PROFILE_64GB).get_max_desktops() == 2
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "-1")
    with pytest.raises(ValueError, match="non-negative integer"):
        HardwareProfiler(profile=PROFILE_16GB)

    # Dynamic scaling to 32GB
    profiler.set_profile("32GB")
    assert profiler.get_max_desktops() == 8
    assert profiler.active_profile.total_ram_gb == 32.0

    # Dynamic scaling to 64GB without rewriting architecture
    profiler.set_profile("64GB")
    assert profiler.get_max_desktops() == 16
    assert profiler.get_headroom_gb() == 6.0
    assert profiler.active_profile.total_ram_gb == 64.0

    # Back to 16GB
    profiler.set_profile(PROFILE_16GB)
    assert profiler.get_max_desktops() == 4


@pytest.mark.asyncio
async def test_deployment_capacity_ceiling_limits_profile_and_queues(monkeypatch):
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "1")
    monitor = ResourceMonitor()
    monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB),
        monitor=monitor,
    )

    first = await scheduler.request_desktop_session("EMP-DEPLOY-1", "TASK-1")
    second = await scheduler.request_desktop_session("EMP-DEPLOY-2", "TASK-2")

    assert first["status"] == "active"
    assert second["status"] == "queued_standby"
    assert second["reason"] == "Active desktop capacity full (1/1 slots)"
    assert scheduler.get_stats()["max_slots"] == 1
    await scheduler.emergency_capacity_shutdown()


@pytest.mark.asyncio
async def test_desktop_slot_pool_and_ports():
    pool = DesktopSlotPool(max_slots=4)
    assert len(pool._slots) == 4

    # Verify sequential port assignments
    assert pool._slots[1].vnc_port == 5901
    assert pool._slots[1].novnc_port == 6081
    assert pool._slots[4].vnc_port == 5904
    assert pool._slots[4].novnc_port == 6084

    # Provision slot 1
    slot = await pool.provision_slot(1, "EMP-UNIT-1")
    assert slot.employee_id == "EMP-UNIT-1"
    assert not slot.is_free
    assert pool.get_active_count() == 1

    # Release slot 1
    released = await pool.release_slot(1)
    assert released is True
    assert slot.is_free
    assert pool.get_active_count() == 0


def test_session_lease_lifecycle():
    manager = SessionLeaseManager(default_ttl_seconds=10.0)
    lease = manager.grant(slot_id=1, employee_id="EMP-TEST", ttl_seconds=2.0)

    assert not lease.is_expired()
    assert manager.get_lease(1) is not None

    # Heartbeat
    ok = manager.heartbeat(1)
    assert ok is True

    # Revoke
    revoked = manager.revoke(1)
    assert revoked is not None
    assert manager.get_lease(1) is None


def test_checkpoint_manager_durability_and_resumption():
    temp_dir = tempfile.mkdtemp()
    try:
        ck_mgr = CheckpointManager(base_dir=temp_dir)

        path = ck_mgr.save_checkpoint(
            employee_id="EMP-CK-1",
            task_id="TASK-PERSIST-1",
            step_index=7,
            state_dict={"subtask": "writing unit tests", "tests_passing": 14},
            pending_operations=[{"action": "commit_code"}],
            workspace_path="/tmp/test_workspace",
        )

        assert os.path.exists(path)
        assert ck_mgr.has_checkpoint("EMP-CK-1", "TASK-PERSIST-1")

        loaded = ck_mgr.load_checkpoint("EMP-CK-1", "TASK-PERSIST-1")
        assert loaded is not None
        assert loaded["version"] == 2
        assert loaded["integrity_verified"] is True
        assert len(loaded["integrity_sha256"]) == 64
        assert loaded["step_index"] == 7
        assert loaded["state_dict"]["tests_passing"] == 14
        assert len(loaded["pending_operations"]) == 1

        # Corrupt saved state is not accepted for resumption.
        with open(path, "r+", encoding="utf-8") as f:
            saved = json.load(f)
            saved["state_dict"]["tests_passing"] = 99
            f.seek(0)
            json.dump(saved, f)
            f.truncate()
        assert ck_mgr.load_checkpoint("EMP-CK-1", "TASK-PERSIST-1") is None
        assert ck_mgr.has_checkpoint("EMP-CK-1", "TASK-PERSIST-1") is False

        # Clear checkpoint
        cleared = ck_mgr.clear_checkpoint("EMP-CK-1", "TASK-PERSIST-1")
        assert cleared is True
        assert not ck_mgr.has_checkpoint("EMP-CK-1", "TASK-PERSIST-1")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_checkpoint_manager_rejects_employee_path_traversal(tmp_path):
    ck_mgr = CheckpointManager(base_dir=str(tmp_path / "checkpoints"))
    with pytest.raises(ValueError, match="safe path component"):
        ck_mgr.save_checkpoint("../outside", "TASK-1", step_index=1, state_dict={})
    with pytest.raises(ValueError, match="safe path component"):
        ck_mgr.list_employee_checkpoints("../outside")
    assert not (tmp_path / "outside").exists()


@pytest.mark.asyncio
async def test_scheduler_keeps_legacy_checkpoint_readable_but_does_not_resume_it(tmp_path):
    checkpoint_dir = str(tmp_path / "checkpoints")
    ck_mgr = CheckpointManager(base_dir=checkpoint_dir)
    path = ck_mgr.save_checkpoint(
        employee_id="EMP-LEGACY",
        task_id="TASK-LEGACY",
        step_index=4,
        state_dict={"progress": "old-format"},
    )
    with open(path, "r+", encoding="utf-8") as f:
        legacy = json.load(f)
        legacy.pop("integrity_sha256")
        legacy["version"] = 1
        f.seek(0)
        json.dump(legacy, f)
        f.truncate()

    retained = ck_mgr.load_checkpoint("EMP-LEGACY", "TASK-LEGACY")
    assert retained is not None
    assert retained["integrity_verified"] is False

    monitor = ResourceMonitor()
    monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB, max_active_desktops=1),
        monitor=monitor,
        checkpoint_manager=CheckpointManager(base_dir=checkpoint_dir),
    )
    result = await scheduler.request_desktop_session("EMP-LEGACY", "TASK-LEGACY")

    assert result["resumed_from_checkpoint"] is False
    assert result["checkpoint_available_unverified"] is True
    assert result["checkpoint"] is None
    await scheduler.emergency_capacity_shutdown()


@pytest.mark.asyncio
async def test_scheduler_returns_verified_checkpoint_state_for_resume(tmp_path):
    checkpoint_dir = str(tmp_path / "checkpoints")
    ck_mgr = CheckpointManager(base_dir=checkpoint_dir)
    ck_mgr.save_checkpoint(
        employee_id="EMP-RESUME",
        task_id="TASK-RESUME",
        step_index=12,
        state_dict={"draft": "chapter 3", "verified": True},
        pending_operations=[{"operation": "review chapter 3"}],
        workspace_path="persistent/EMP-RESUME",
    )
    monitor = ResourceMonitor()
    monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB, max_active_desktops=1),
        monitor=monitor,
        checkpoint_manager=CheckpointManager(base_dir=checkpoint_dir),
    )

    result = await scheduler.request_desktop_session("EMP-RESUME", "TASK-RESUME")

    assert result["status"] == "active"
    assert result["resumed_from_checkpoint"] is True
    assert result["checkpoint_step"] == 12
    assert result["checkpoint"]["state_dict"] == {"draft": "chapter 3", "verified": True}
    assert result["checkpoint"]["pending_operations"] == [{"operation": "review chapter 3"}]
    await scheduler.emergency_capacity_shutdown()


def test_employee_execution_queue_prioritization():
    queue = EmployeeExecutionQueue()

    queue.enqueue("EMP-LOW", "TASK-L", department="Eng", priority=TaskPriority.LOW)
    queue.enqueue("EMP-CRIT", "TASK-C", department="Sec", priority=TaskPriority.CRITICAL)
    queue.enqueue("EMP-MED", "TASK-M", department="Eng", priority=TaskPriority.MEDIUM)
    queue.enqueue("EMP-HIGH", "TASK-H", department="QA", priority=TaskPriority.HIGH)

    assert queue.size() == 4

    # CRITICAL should be dequeued first
    first = queue.dequeue()
    assert first.employee_id == "EMP-CRIT"

    # HIGH next
    second = queue.dequeue()
    assert second.employee_id == "EMP-HIGH"

    # MEDIUM next
    third = queue.dequeue()
    assert third.employee_id == "EMP-MED"

    # LOW last
    fourth = queue.dequeue()
    assert fourth.employee_id == "EMP-LOW"

    assert queue.is_empty()


@pytest.mark.asyncio
async def test_resource_scheduler_four_slot_limit_and_standby():
    temp_dir = tempfile.mkdtemp()
    try:
        ck_mgr = CheckpointManager(base_dir=temp_dir)
        profiler = HardwareProfiler(profile=PROFILE_16GB)
        monitor = ResourceMonitor()
        monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
        scheduler = DesktopResourceScheduler(
            profiler=profiler,
            monitor=monitor,
            checkpoint_manager=ck_mgr,
        )

        # Allocate 4 active desktop slots (Capacity limit on 16GB)
        r1 = await scheduler.request_desktop_session("EMP-1", "TASK-1")
        r2 = await scheduler.request_desktop_session("EMP-2", "TASK-2")
        r3 = await scheduler.request_desktop_session("EMP-3", "TASK-3")
        r4 = await scheduler.request_desktop_session("EMP-4", "TASK-4")

        assert r1["status"] == "active"
        assert r2["status"] == "active"
        assert r3["status"] == "active"
        assert r4["status"] == "active"

        stats = scheduler.get_stats()
        assert stats["active_desktops_count"] == 4
        assert stats["max_slots"] == 4

        # 5th employee requesting desktop slot: CAPACITY IS FULL!
        # Must be enqueued as STANDBY without launching a container
        r5 = await scheduler.request_desktop_session("EMP-5", "TASK-5", priority=TaskPriority.HIGH)
        assert r5["status"] == "queued_standby"
        assert r5["employee_id"] == "EMP-5"
        assert "Active desktop capacity full" in r5["reason"]

        stats_after = scheduler.get_stats()
        assert stats_after["active_desktops_count"] == 4
        assert stats_after["standby_employees_count"] == 1
        assert stats_after["queue_depth"] == 1
        assert "EMP-5" in stats_after["standby_employees"]

        # Clean up
        await scheduler.emergency_capacity_shutdown()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_resource_scheduler_suspension_checkpoint_and_standby_promotion():
    temp_dir = tempfile.mkdtemp()
    try:
        ck_mgr = CheckpointManager(base_dir=temp_dir)
        monitor = ResourceMonitor()
        monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
        scheduler = DesktopResourceScheduler(
            profiler=HardwareProfiler(profile=PROFILE_16GB),
            monitor=monitor,
            checkpoint_manager=ck_mgr,
        )

        # Fill 4 slots
        for i in range(1, 5):
            await scheduler.request_desktop_session(f"EMP-{i}", f"TASK-{i}")

        # Enqueue EMP-5 into standby
        await scheduler.request_desktop_session("EMP-5", "TASK-5", priority=TaskPriority.CRITICAL)

        # Safely suspend EMP-1 with durable checkpoint
        suspend_res = await scheduler.suspend_and_release(
            employee_id="EMP-1",
            task_id="TASK-1",
            current_step=42,
            state_dict={"progress": "halfway"},
        )

        assert suspend_res["status"] == "suspended_to_standby"
        assert suspend_res["checkpoint_saved"] is True
        assert os.path.exists(suspend_res["checkpoint_path"])

        # Check that EMP-5 was automatically promoted from standby to the freed slot!
        dispatched = suspend_res["dispatched_next"]
        assert dispatched is not None
        assert dispatched["status"] == "active"
        assert "EMP-5" in scheduler._active_employees
        assert "EMP-1" in scheduler._standby_employees

        # Clean up
        await scheduler.emergency_capacity_shutdown()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_resource_scheduler_admission_control_and_emergency_shutdown():
    monitor = ResourceMonitor()
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB),
        monitor=monitor,
    )

    # Simulate insufficient host RAM headroom (e.g. only 0.5 GB available < 1.5 GB required)
    monitor.set_mock_metrics(free_ram_gb=0.5, cpu_percent=40.0)

    # Request session should be rejected by admission controller and queued in standby
    res = await scheduler.request_desktop_session("ANC-EMP-DENIED", "TASK-D")
    assert res["status"] == "queued_standby"
    assert "Insufficient RAM headroom" in res["reason"]

    # Clear mock metrics to restore healthy host state
    monitor.clear_mock_metrics()

    # Now allocation succeeds
    res_ok = await scheduler.request_desktop_session("ANC-EMP-OK", "TASK-OK")
    assert res_ok["status"] == "active"

    # Emergency shutdown: safely suspends all active sessions without re-dispatching standby
    evacuated = await scheduler.emergency_capacity_shutdown()
    assert evacuated >= 1
    assert scheduler.slot_pool.get_active_count() == 0


@pytest.mark.asyncio
async def test_resource_scheduler_expired_lease_reconciliation_and_fair_reclaim():
    temp_dir = tempfile.mkdtemp()
    try:
        ck_mgr = CheckpointManager(base_dir=temp_dir)
        monitor = ResourceMonitor()
        monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
        scheduler = DesktopResourceScheduler(
            profiler=HardwareProfiler(profile=PROFILE_16GB),
            monitor=monitor,
            checkpoint_manager=ck_mgr,
        )

        # Fill 4 slots
        for i in range(1, 5):
            await scheduler.request_desktop_session(f"ANC-EMP-{i}", f"TASK-{i}")

        assert scheduler.slot_pool.get_active_count() == 4

        # Enqueue 5th employee into standby (capacity full, none expired yet)
        res5 = await scheduler.request_desktop_session("ANC-EMP-5", "TASK-5", priority=TaskPriority.HIGH)
        assert res5["status"] == "queued_standby"
        assert "ANC-EMP-5" in scheduler._standby_employees

        # Manually expire lease for slot 1 (occupied by ANC-EMP-1)
        slot1_id = scheduler._active_employees["ANC-EMP-1"]
        lease = scheduler.lease_manager.get_lease(slot1_id)
        lease.last_heartbeat = 0.0  # Force expiration
        assert lease.is_expired()

        # Reconcile expired leases: should evict ANC-EMP-1 to standby and automatically promote ANC-EMP-5 to active!
        reclaimed = await scheduler.reconcile_expired_leases()
        assert slot1_id in reclaimed
        assert "ANC-EMP-1" in scheduler._standby_employees
        assert "ANC-EMP-5" in scheduler._active_employees

        # Check durable checkpoint was written for the evicted employee
        assert ck_mgr.has_checkpoint("ANC-EMP-1", "lease_expired")

        await scheduler.emergency_capacity_shutdown()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_resource_scheduler_concurrent_multi_lease_expiration():
    """Edge Case 1: All 4 active desktop sessions expire simultaneously under sudden host throttling."""
    temp_dir = tempfile.mkdtemp()
    try:
        ck_mgr = CheckpointManager(base_dir=temp_dir)
        monitor = ResourceMonitor()
        monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
        scheduler = DesktopResourceScheduler(
            profiler=HardwareProfiler(profile=PROFILE_16GB),
            monitor=monitor,
            checkpoint_manager=ck_mgr,
        )

        # Fill all 4 slots
        for i in range(1, 5):
            await scheduler.request_desktop_session(f"ANC-EMP-BATCH-{i}", f"TASK-B{i}")

        assert scheduler.slot_pool.get_active_count() == 4

        # Enqueue 2 standby employees
        await scheduler.request_desktop_session("ANC-EMP-WAIT-1", "TASK-W1", priority=TaskPriority.HIGH)
        await scheduler.request_desktop_session("ANC-EMP-WAIT-2", "TASK-W2", priority=TaskPriority.HIGH)
        assert scheduler.queue.size() == 2

        # Simultaneously expire all 4 leases
        for slot_id in range(1, 5):
            lease = scheduler.lease_manager.get_lease(slot_id)
            lease.last_heartbeat = 0.0

        # Run reconciliation: all 4 slots reclaimed, 2 standby workers promoted
        reclaimed = await scheduler.reconcile_expired_leases()
        assert len(reclaimed) == 4

        # 2 standby workers must now be active
        assert "ANC-EMP-WAIT-1" in scheduler._active_employees
        assert "ANC-EMP-WAIT-2" in scheduler._active_employees
        assert scheduler.queue.size() == 0
        assert scheduler.slot_pool.get_active_count() == 2

        await scheduler.emergency_capacity_shutdown()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_desktop_slot_pool_downscaling_and_slot_confinement():
    """Verifies that downscaling profile from 16 to 4 slots purges free slots and restricts allocation."""
    pool = DesktopSlotPool(max_slots=16)
    assert len(pool.list_slots()) == 16

    # Provision slot 1
    await pool.provision_slot(1, "EMP-PERSIST")
    assert pool.get_active_count() == 1

    # Downscale to 4 slots
    pool.set_max_slots(4)
    assert pool.max_slots == 4

    # list_slots must only return 4 slots
    slots_list = pool.list_slots()
    assert len(slots_list) == 4
    assert all(s["slot_id"] <= 4 for s in slots_list)

    # get_free_slot must return a slot <= 4
    free_slot = pool.get_free_slot()
    assert free_slot is not None
    assert free_slot.slot_id <= 4
    assert free_slot.slot_id != 1  # Slot 1 is busy

    # Active count must only count slots <= 4
    assert pool.get_active_count() == 1


@pytest.mark.asyncio
async def test_reconcile_expired_leases_orphan_slot_cleanup():
    """Verifies that an expired lease with no active employee still calls release_slot and marks slot free."""
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB),
    )

    # Manually occupy slot 2 in slot_pool without entering _active_employees (orphan condition)
    slot2 = scheduler.slot_pool._slots[2]
    slot2.status = "active"
    slot2.employee_id = None
    assert not slot2.is_free

    # Grant an expired lease for slot 2
    lease = scheduler.lease_manager.grant(slot_id=2, employee_id="GHOST_EMP", ttl_seconds=1.0)
    lease.last_heartbeat = 0.0
    assert lease.is_expired()

    # Reconcile expired leases: should revoke lease and release the orphan slot in slot_pool
    reclaimed = await scheduler.reconcile_expired_leases()
    assert 2 in reclaimed
    assert slot2.is_free
    assert scheduler.lease_manager.get_lease(2) is None


@pytest.mark.asyncio
async def test_resource_scheduler_certify_one_two_three_four_workloads():
    """
    Certifies 1, 2, 3, and 4 desktop workloads under load.
    Confirms strictly four active desktop sessions on 16 GB profile, then 5th queues in standby.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        ck_mgr = CheckpointManager(base_dir=temp_dir)
        monitor = ResourceMonitor()
        monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
        scheduler = DesktopResourceScheduler(
            profiler=HardwareProfiler(profile=PROFILE_16GB),
            monitor=monitor,
            checkpoint_manager=ck_mgr,
        )

        # 1. Certify 1 Desktop Workload
        r1 = await scheduler.request_desktop_session("EMP-W1", "TASK-1")
        assert r1["status"] == "active"
        assert scheduler.slot_pool.get_active_count() == 1

        # 2. Certify 2 Desktop Workload
        r2 = await scheduler.request_desktop_session("EMP-W2", "TASK-2")
        assert r2["status"] == "active"
        assert scheduler.slot_pool.get_active_count() == 2

        # 3. Certify 3 Desktop Workload
        r3 = await scheduler.request_desktop_session("EMP-W3", "TASK-3")
        assert r3["status"] == "active"
        assert scheduler.slot_pool.get_active_count() == 3

        # 4. Certify 4 Desktop Workload (Maximum Configured Capacity on 16GB)
        r4 = await scheduler.request_desktop_session("EMP-W4", "TASK-4")
        assert r4["status"] == "active"
        assert scheduler.slot_pool.get_active_count() == 4

        # 5. Fifth workload must enter standby queue without launching a container
        r5 = await scheduler.request_desktop_session("EMP-W5", "TASK-5")
        assert r5["status"] == "queued_standby"
        assert scheduler.slot_pool.get_active_count() == 4
        assert scheduler.queue.size() == 1

        # Clean up
        await scheduler.emergency_capacity_shutdown()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


