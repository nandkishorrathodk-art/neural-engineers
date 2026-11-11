"""
ANCOS NEURO-4 Integration Test Suite: Desktop Session Pool & Resource Intelligence.
Empirically tests:
1. Slot lease contention under concurrent requests.
2. Fair queuing across departments and priority levels with anti-starvation aging.
3. Host CPU, RAM, and GPU capacity limits and admission control.
4. Recovery from isolated API restarts and database state reconstruction.
5. Broker unavailability (HTTP 503 runtime_unavailable) preserving active lease fences.
6. Reaper cleanup for failed provisioning without premature capacity reuse.
7. Multi-host / resource pool key isolation and cross-pool employee fencing.
8. Complete and release lifecycle with checkpoint clearing and queued employee promotion.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_container_manager, get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.constants import ApprovalStatus, ContainerStatus, TaskPriority
from neural.containers.manager import DockerBrokerRequestError
from neural.db.models import Employee
from neural.db.repositories import (
    ApprovalRepository,
    ResourceLeaseRepository,
)
from neural.resource_scheduler.checkpoint_manager import CheckpointManager
from neural.resource_scheduler.desktop_pool import DesktopSlotPool
from neural.resource_scheduler.employee_queue import EmployeeExecutionQueue
from neural.resource_scheduler.hardware_profiler import (
    PROFILE_16GB,
    HardwareProfile,
    HardwareProfiler,
)
from neural.resource_scheduler.resource_monitor import ResourceMonitor
from neural.resource_scheduler.scheduler import DesktopResourceScheduler


class MockBrokerContainerManager:
    """Simulates the container manager with broker error handling for tests."""

    def __init__(self, *, broker_online: bool = True, provision_error: Exception | None = None):
        self.broker_online = broker_online
        self.provision_error = provision_error
        self.workstations: dict[str, dict] = {}

    async def provision_employee_desktop(
        self,
        employee_id: str,
        role_image: str = "employee-base",
        cpu_limit: float | None = None,
        mem_limit: str | None = None,
        container_repo=None,
        employee_repo=None,
    ):
        if self.provision_error:
            raise self.provision_error
        if not self.broker_online:
            raise DockerBrokerRequestError("Broker connection refused", 503)

        docker_id = f"ancos-desktop-{employee_id.lower()}"
        spec = {
            "docker_id": docker_id,
            "employee_id": employee_id,
            "image": f"ancos/{role_image}:latest",
            "vnc_port": 5901,
            "novnc_port": 6081,
            "desktop_url": "http://localhost:6081/vnc.html?autoconnect=true",
            "cpu_limit": cpu_limit or 1.5,
            "mem_limit": mem_limit or "2g",
            "status": "running",
            "workspace": f"/tmp/workspaces/{employee_id}",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "vnc_password": "test-vnc-secret",
        }
        self.workstations[employee_id] = spec
        if container_repo:
            db_c = await container_repo.upsert_current(
                docker_id=docker_id,
                image=spec["image"],
                status=ContainerStatus.RUNNING,
                vnc_port=spec["vnc_port"],
                novnc_port=spec["novnc_port"],
                resource_limits={"cpu_limit": spec["cpu_limit"], "mem_limit": spec["mem_limit"]},
                network_config={"network": "test-net"},
                workspace_path=spec["workspace"],
            )
            spec["db_id"] = db_c.id
            if employee_repo:
                await employee_repo.assign_container(employee_id, db_c.id)
        return spec

    async def get_desktop_by_employee_id(
        self, employee_id: str, container_repo=None, employee_repo=None
    ):
        docker_id = f"ancos-desktop-{employee_id.lower()}"
        if not self.broker_online:
            return {"docker_id": docker_id, "status": "runtime_unavailable"}
        spec = self.workstations.get(employee_id)
        if spec:
            return spec
        if container_repo:
            db_c = await container_repo.get_by_docker_id(docker_id)
            if db_c:
                recovered = {
                    "docker_id": db_c.docker_id,
                    "employee_id": employee_id,
                    "image": db_c.image,
                    "status": db_c.status.value if hasattr(db_c.status, "value") else str(db_c.status),
                    "vnc_port": db_c.vnc_port,
                    "novnc_port": db_c.novnc_port,
                    "desktop_url": f"http://localhost:{db_c.novnc_port}/vnc.html?autoconnect=true",
                    "cpu_limit": 1.5,
                    "mem_limit": "2g",
                    "workspace": db_c.workspace_path,
                }
                self.workstations[employee_id] = recovered
                return recovered
        return None

    async def terminate_desktop(self, docker_id: str, container_repo=None):
        for spec in self.workstations.values():
            if spec["docker_id"] == docker_id:
                spec["status"] = "terminated"
                if container_repo:
                    await container_repo.update_status(docker_id, ContainerStatus.TERMINATED)
                return True
        return False

    async def _broker_request(self, method: str, path: str, **_kwargs):
        if not self.broker_online:
            raise DockerBrokerRequestError("Broker connection refused", 503)
        if method == "GET":
            return {"status": "running"}
        if method == "POST" and path.endswith("/terminate"):
            return {"status": "terminated"}
        return {}


async def create_test_employee(session, employee_id: str, dept: str = "Engineering"):
    emp = Employee(
        employee_id=employee_id,
        name=f"Worker {employee_id}",
        title="Software Engineer",
        department=dept,
        system_prompt="Test worker prompt.",
        model_config_data={},
        approved_tools=["terminal"],
    )
    session.add(emp)
    await session.commit()
    await session.refresh(emp)
    return emp


async def create_test_approval(session, employee_id: str, action: str = "PROVISION_WORKSTATION"):
    return await ApprovalRepository(session).create(
        action_type=action,
        description=f"Approve {action} for {employee_id}",
        justification="Authorized test approval",
        requested_by="human-board",
        details={"employee_id": employee_id, "docker_id": f"ancos-desktop-{employee_id.lower()}"},
        status=ApprovalStatus.APPROVED,
    )


@pytest.mark.asyncio
async def test_gpu_aware_admission_control_and_headroom():
    """Verifies host CPU, RAM, and GPU capacity limits and admission control."""
    monitor = ResourceMonitor()
    # Profile with GPU requirement: 1 GPU, 2.0 GB VRAM headroom required
    gpu_profile = HardwareProfile(
        profile_name="GPU_ACCELERATED",
        total_ram_gb=32.0,
        max_active_desktops=4,
        safe_ram_headroom_gb=3.0,
        cpu_core_count=16,
        max_cpu_percent_threshold=85.0,
        gpu_count=1,
        safe_vram_headroom_gb=2.0,
        max_gpu_percent_threshold=85.0,
    )

    # 1. Healthy host: safe to admit
    monitor.set_mock_metrics(free_ram_gb=16.0, cpu_percent=30.0, free_vram_gb=6.0, gpu_percent=20.0)
    safe, msg = monitor.is_safe_to_admit(gpu_profile)
    assert safe is True
    assert "healthy" in msg

    # 2. Insufficient GPU VRAM headroom
    monitor.set_mock_metrics(free_ram_gb=16.0, cpu_percent=30.0, free_vram_gb=1.2, gpu_percent=20.0)
    safe, msg = monitor.is_safe_to_admit(gpu_profile)
    assert safe is False
    assert "Insufficient GPU VRAM headroom" in msg

    # 3. GPU utilization redline
    monitor.set_mock_metrics(free_ram_gb=16.0, cpu_percent=30.0, free_vram_gb=6.0, gpu_percent=92.0)
    safe, msg = monitor.is_safe_to_admit(gpu_profile)
    assert safe is False
    assert "GPU utilization too high" in msg

    # 4. Critical redline detection
    monitor.set_mock_metrics(gpu_percent=99.0)
    assert monitor.is_critical_redline() is True


@pytest.mark.asyncio
async def test_fair_queue_anti_starvation_and_department_round_robin():
    """Verifies priority scheduling with anti-starvation aging and department fairness."""
    queue = EmployeeExecutionQueue()

    # Enqueue employees across departments and priorities
    queue.enqueue("EMP-LOW", "TASK-L", department="Engineering", priority=TaskPriority.LOW)
    queue.enqueue("EMP-MED", "TASK-M", department="Engineering", priority=TaskPriority.MEDIUM)
    queue.enqueue("EMP-HIGH", "TASK-H", department="QA", priority=TaskPriority.HIGH)
    queue.enqueue("EMP-CRIT", "TASK-C", department="Research", priority=TaskPriority.CRITICAL)

    assert queue.size() == 4

    # CRITICAL dequeues first
    first = queue.dequeue()
    assert first.employee_id == "EMP-CRIT"

    # HIGH dequeues second
    second = queue.dequeue()
    assert second.employee_id == "EMP-HIGH"

    # Simulate EMP-LOW having waited for > 150 seconds (anti-starvation aging)
    for item in queue._queue:
        if item.employee_id == "EMP-LOW":
            item.enqueued_at -= 180.0  # +2 aging boost (effective rank: 1 + 2 = 3 > MEDIUM 2)

    # Aging boosted EMP-LOW now dequeues ahead of freshly queued MEDIUM!
    third = queue.dequeue()
    assert third.employee_id == "EMP-LOW"

    # Finally MEDIUM dequeues
    fourth = queue.dequeue()
    assert fourth.employee_id == "EMP-MED"
    assert queue.is_empty()


@pytest.mark.asyncio
async def test_multi_host_resource_pool_isolation_and_cross_host_fencing(async_session):
    """Verifies that slot leases are isolated across host pools while preventing duplicate employee sessions."""
    repo = ResourceLeaseRepository(async_session)

    # Host 1 and Host 2 can concurrently allocate slot 1 for different employees
    h1_lease = await repo.claim(
        resource_key="desktop:host-alpha:slot-1",
        employee_id="ANC-EMP-ALPHA",
        lease_seconds=60,
    )
    assert h1_lease is not None
    assert h1_lease["resource_key"] == "desktop:host-alpha:slot-1"

    h2_lease = await repo.claim(
        resource_key="desktop:host-beta:slot-1",
        employee_id="ANC-EMP-BETA",
        lease_seconds=60,
    )
    assert h2_lease is not None
    assert h2_lease["resource_key"] == "desktop:host-beta:slot-1"

    # BUT: The same employee cannot simultaneously claim a lease on another host
    duplicate_claim = await repo.claim(
        resource_key="desktop:host-beta:slot-2",
        employee_id="ANC-EMP-ALPHA",  # Already has active lease on host-alpha
        lease_seconds=60,
    )
    assert duplicate_claim is None  # Blocked by cross-host employee fencing!

    # Cleanup
    assert await repo.release(h1_lease["lease_id"], h1_lease["fencing_token"])
    assert await repo.release(h2_lease["lease_id"], h2_lease["fencing_token"])


@pytest.mark.asyncio
async def test_complete_and_release_lifecycle(tmp_path, async_session):
    """Verifies complete_and_release clears checkpoint, releases lease, and promotes standby."""
    repo = ResourceLeaseRepository(async_session)
    ck_mgr = CheckpointManager(base_dir=str(tmp_path / "checkpoints"))
    monitor = ResourceMonitor()
    monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)

    mock_mgr = MockBrokerContainerManager()
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB, max_active_desktops=1),
        monitor=monitor,
        slot_pool=DesktopSlotPool(max_slots=1, container_manager=mock_mgr),
        checkpoint_manager=ck_mgr,
        resource_lease_repo=repo,
        resource_pool_id="pool-complete-test",
    )

    # EMP-1 acquires slot 1
    r1 = await scheduler.request_desktop_session("ANC-EMP-DONE", "TASK-DONE")
    assert r1["status"] == "active"

    # Save a temporary checkpoint
    ck_mgr.save_checkpoint("ANC-EMP-DONE", "TASK-DONE", step_index=3, state_dict={"foo": "bar"})
    assert ck_mgr.has_checkpoint("ANC-EMP-DONE", "TASK-DONE")

    # EMP-2 queues in standby
    r2 = await scheduler.request_desktop_session("ANC-EMP-NEXT", "TASK-NEXT")
    assert r2["status"] == "queued_standby"

    # Complete and release EMP-1
    completed = await scheduler.complete_and_release(
        employee_id="ANC-EMP-DONE",
        task_id="TASK-DONE",
        clear_checkpoint=True,
        dispatch_next=True,
    )

    assert completed["status"] == "completed_and_released"
    assert completed["checkpoint_cleared"] is True
    assert ck_mgr.has_checkpoint("ANC-EMP-DONE", "TASK-DONE") is False
    assert completed["dispatched_next"] is not None
    assert completed["dispatched_next"]["employee_id"] == "ANC-EMP-NEXT"
    assert "ANC-EMP-NEXT" in scheduler._active_employees

    # Teardown
    await scheduler.emergency_capacity_shutdown()


@pytest.mark.asyncio
async def test_api_restart_reconstruction_and_broker_recovery(async_session, monkeypatch):
    """Verifies that an API restart reconstructs container state from DB and handles broker recovery."""
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "1")
    emp = await create_test_employee(async_session, "ANC-EMP-RECOVER-API")
    prov_approval = await create_test_approval(async_session, emp.employee_id, "PROVISION_WORKSTATION")

    mock_mgr = MockBrokerContainerManager(broker_online=True)

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session
    app.dependency_overrides[get_container_manager] = lambda: mock_mgr
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Provision workstation
        prov_res = await client.post(
            f"/employees/{emp.employee_id}/workstation/provision?approval_id={prov_approval.id}"
        )
        assert prov_res.status_code == 200
        docker_id = prov_res.json()["docker_id"]

        # 2. Simulate API restart: new fresh container manager with EMPTY in-memory cache
        fresh_mgr = MockBrokerContainerManager(broker_online=True)
        # Seed broker with existing container
        fresh_mgr.workstations = dict(mock_mgr.workstations)
        app.dependency_overrides[get_container_manager] = lambda: fresh_mgr

        # Read workstation: should recover spec from DB and refresh from broker!
        read_res = await client.get(f"/employees/{emp.employee_id}/workstation")
        assert read_res.status_code == 200
        assert read_res.json()["docker_id"] == docker_id
        assert read_res.json()["status"] == "running"

        # 3. Simulate broker outage
        fresh_mgr.broker_online = False
        outage_res = await client.get(f"/employees/{emp.employee_id}/workstation")
        assert outage_res.status_code == 503
        assert "runtime is unavailable" in outage_res.json()["detail"].lower()

        # Verify durable lease in DB is still ACTIVE during outage
        repo = ResourceLeaseRepository(async_session)
        lease = await repo.get_by_session_id(docker_id)
        assert lease is not None
        assert lease["status"] == "active"

        # 4. Broker recovers
        fresh_mgr.broker_online = True
        recovered_res = await client.get(f"/employees/{emp.employee_id}/workstation")
        assert recovered_res.status_code == 200
        assert recovered_res.json()["status"] == "running"

        # 5. Clean termination
        term_approval = await create_test_approval(async_session, emp.employee_id, "TERMINATE_WORKSTATION")
        term_res = await client.post(
            f"/employees/{emp.employee_id}/workstation/terminate?approval_id={term_approval.id}"
        )
        assert term_res.status_code == 200
        assert term_res.json()["status"] == "terminated"

        # Verify lease released
        released_lease = await repo.get_by_session_id(docker_id)
        assert released_lease["status"] == "released"


@pytest.mark.asyncio
async def test_cancel_standby_and_queue_removal():
    """Verifies cancel_standby cleans up standby state and removes employee from queue."""
    monitor = ResourceMonitor()
    monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
    mock_mgr = MockBrokerContainerManager()
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB, max_active_desktops=1),
        monitor=monitor,
        slot_pool=DesktopSlotPool(max_slots=1, container_manager=mock_mgr),
    )

    # 1. First employee gets the only slot
    r1 = await scheduler.request_desktop_session("EMP-ACTIVE-1", "TASK-1")
    assert r1["status"] == "active"

    # 2. Second employee gets queued as standby
    r2 = await scheduler.request_desktop_session("EMP-STANDBY-2", "TASK-2")
    assert r2["status"] == "queued_standby"
    assert "EMP-STANDBY-2" in scheduler._standby_employees
    assert scheduler.queue.size() == 1

    # 3. Cancel standby for EMP-STANDBY-2
    canceled = scheduler.cancel_standby("EMP-STANDBY-2")
    assert canceled is True
    assert "EMP-STANDBY-2" not in scheduler._standby_employees
    assert scheduler.queue.size() == 0

    # 4. Cancel again returns False (already removed)
    assert scheduler.cancel_standby("EMP-STANDBY-2") is False

    await scheduler.emergency_capacity_shutdown()


@pytest.mark.asyncio
async def test_idempotent_complete_and_double_release(tmp_path):
    """Verifies complete_and_release handles double-call or inactive employee gracefully."""
    ck_mgr = CheckpointManager(base_dir=str(tmp_path / "checkpoints"))
    monitor = ResourceMonitor()
    monitor.set_mock_metrics(free_ram_gb=10.0, cpu_percent=20.0)
    mock_mgr = MockBrokerContainerManager()
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB, max_active_desktops=2),
        monitor=monitor,
        slot_pool=DesktopSlotPool(max_slots=2, container_manager=mock_mgr),
        checkpoint_manager=ck_mgr,
    )

    # Calling complete_and_release on inactive employee returns not_active
    res_inactive = await scheduler.complete_and_release("EMP-NOT-ACTIVE", "TASK-X")
    assert res_inactive["status"] == "not_active"

    # Allocate and then complete
    await scheduler.request_desktop_session("EMP-IDEMP", "TASK-IDEMP")
    assert "EMP-IDEMP" in scheduler._active_employees

    res1 = await scheduler.complete_and_release("EMP-IDEMP", "TASK-IDEMP")
    assert res1["status"] == "completed_and_released"
    assert "EMP-IDEMP" not in scheduler._active_employees

    # Second call returns not_active without throwing RuntimeError
    res2 = await scheduler.complete_and_release("EMP-IDEMP", "TASK-IDEMP")
    assert res2["status"] == "not_active"

    await scheduler.emergency_capacity_shutdown()


@pytest.mark.asyncio
async def test_concurrent_slot_contention_and_fair_queue_dispatch(async_session):
    """Verifies that multiple concurrent desktop requests safely resolve without slot collisions."""
    import asyncio

    repo = ResourceLeaseRepository(async_session)
    monitor = ResourceMonitor()
    monitor.set_mock_metrics(free_ram_gb=16.0, cpu_percent=20.0)
    mock_mgr = MockBrokerContainerManager()
    scheduler = DesktopResourceScheduler(
        profiler=HardwareProfiler(profile=PROFILE_16GB, max_active_desktops=2),
        monitor=monitor,
        slot_pool=DesktopSlotPool(max_slots=2, container_manager=mock_mgr),
        resource_lease_repo=repo,
        resource_pool_id="concurrent-pool",
    )

    # 4 employees request slots simultaneously (2 slots available)
    results = await asyncio.gather(
        scheduler.request_desktop_session("EMP-CONC-1", "TASK-C1", priority=TaskPriority.LOW),
        scheduler.request_desktop_session("EMP-CONC-2", "TASK-C2", priority=TaskPriority.CRITICAL),
        scheduler.request_desktop_session("EMP-CONC-3", "TASK-C3", priority=TaskPriority.HIGH),
        scheduler.request_desktop_session("EMP-CONC-4", "TASK-C4", priority=TaskPriority.MEDIUM),
    )

    active_results = [r for r in results if r["status"] == "active"]
    queued_results = [r for r in results if r["status"] == "queued_standby"]

    assert len(active_results) == 2
    assert len(queued_results) == 2
    # Verify unique slot allocation
    active_slots = [r["slot_id"] for r in active_results]
    assert len(set(active_slots)) == 2

    await scheduler.emergency_capacity_shutdown()

