import asyncio

import pytest
from neural.constants import TaskStatus
from neural.db.repositories import AuditRepository, TaskRepository
from neural.exceptions import EmergencyStopTriggered
from neural.security.kill_switch import (
    get_kill_switch,
    reset_kill_switch,
)
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def clean_kill_switch():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_kill_switch_instant_halt_timing_under_200ms():
    """
    Validates that the Global Emergency Kill Switch halts the entire system
    in strictly less than 200 milliseconds.
    """
    ks = get_kill_switch()
    assert ks.is_halted is False

    receipt = await ks.emergency_halt(reason="Critical Security Anomaly Detected", actor="sec_officer")

    assert receipt["halted"] is True
    assert receipt["status"] == "HALTED"
    assert receipt["halt_latency_ms"] < 200.0, f"Halt latency was {receipt['halt_latency_ms']}ms (>200ms)"
    assert ks.is_halted is True
    assert ks.halt_reason == "Critical Security Anomaly Detected"
    assert ks.halted_by == "sec_officer"


def test_kill_switch_guard_active_raises():
    """Validates that guard_active raises EmergencyStopTriggered when system is halted."""
    ks = get_kill_switch()
    # Nominal state -> does not raise
    ks.guard_active()

    ks.is_halted = True
    ks.halt_reason = "Manual drill"
    ks.halted_by = "admin"

    with pytest.raises(EmergencyStopTriggered) as exc_info:
        ks.guard_active()

    assert "Emergency Stop is active" in str(exc_info.value)
    assert "Manual drill" in str(exc_info.value)


@pytest.mark.asyncio
async def test_kill_switch_cancels_active_worker_tasks_and_freezes_workstations():
    """
    Validates that emergency halt immediately cancels active worker tasks
    and freezes containerized workstations.
    """
    ks = get_kill_switch()

    # Create dummy long-running background task
    async def worker_job():
        await asyncio.sleep(10.0)

    task = asyncio.create_task(worker_job())
    ks.register_worker_task(task)

    # Create mock container manager
    class MockContainerManager:
        def __init__(self):
            self.frozen = False

        async def freeze_all_workstations(self):
            self.frozen = True

        async def unfreeze_all_workstations(self):
            self.frozen = False

    mock_cm = MockContainerManager()
    ks.register_container_manager(mock_cm)

    # Trigger emergency halt
    receipt = await ks.emergency_halt(reason="Rapid containment", actor="operator")

    assert receipt["halted"] is True
    assert receipt["cancelled_workers_count"] >= 1
    assert getattr(task, "cancelling", lambda: 0)() > 0 or task.cancelled() or task.done()
    assert mock_cm.frozen is True


    # Resume system
    resume_receipt = await ks.resume(actor="operator")
    assert resume_receipt["resumed"] is True
    assert resume_receipt["status"] == "RUNNING"
    assert ks.is_halted is False
    assert mock_cm.frozen is False


@pytest.mark.asyncio
async def test_kill_switch_state_recovery_resets_running_tasks(async_session: AsyncSession):
    """
    Validates state recovery mechanism: tasks interrupted mid-flight are reset
    from RUNNING back to PENDING so they can be safely resumed without amnesia.
    """
    ks = get_kill_switch()
    task_repo = TaskRepository(async_session)

    # Create dummy running task
    t = await task_repo.create(
        task_id="TASK-KILL-01",
        project_id="PROJ-01",
        title="Interrupted Task",
        objective="Verify state recovery",
        scope="Core",
        acceptance_criteria="Pass",
        status=TaskStatus.RUNNING,
    )

    # Halt
    await ks.emergency_halt(reason="Incident recovery test", actor="admin")
    assert ks.is_halted is True

    # Resume with state recovery
    resume_receipt = await ks.resume(actor="admin", recovery_strategy="checkpoint", task_repo=task_repo)

    assert resume_receipt["resumed"] is True
    assert resume_receipt["recovered_tasks_count"] >= 1

    # Verify task state in database reset to PENDING
    reloaded = await task_repo.get_by_task_id(t.task_id)
    assert reloaded.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_kill_switch_audit_trail_and_message_bus_integration(async_session: AsyncSession):
    """Validates that emergency halt creates an audit entry and broadcasts an event."""
    ks = get_kill_switch()
    audit_repo = AuditRepository(async_session)

    class MockMessageBus:
        def __init__(self):
            self.events = []

        async def publish_broadcast(self, channel: str, message_type: str, payload: dict, sender: str):
            self.events.append({"channel": channel, "type": message_type, "payload": payload, "sender": sender})

    bus = MockMessageBus()

    await ks.emergency_halt(
        reason="Compromise containment",
        actor="lead_operator",
        audit_repo=audit_repo,
        message_bus=bus,
    )

    # Verify message bus broadcast
    assert len(bus.events) == 1
    assert bus.events[0]["type"] == "emergency_stop"
    assert bus.events[0]["sender"] == "lead_operator"

    # Verify audit log entry
    audit_records = await audit_repo.list_by_action("EMERGENCY_STOP")
    assert len(audit_records) == 1
    assert audit_records[0].actor == "lead_operator"
    assert audit_records[0].resource_type == "system"


@pytest.mark.asyncio
async def test_kill_switch_blocks_boss_brain_and_scheduler(async_session: AsyncSession):
    """Validates that BossBrain and TaskScheduler refuse planning and dispatch while halted."""
    from neural.boss.brain import BossBrain
    from neural.db.repositories import ProjectRepository
    from neural.tasks.scheduler import TaskScheduler

    ks = get_kill_switch()
    p_repo = ProjectRepository(async_session)
    t_repo = TaskRepository(async_session)
    boss = BossBrain(project_repo=p_repo, task_repo=t_repo)
    scheduler = TaskScheduler(task_repo=t_repo)

    # Halt system
    await ks.emergency_halt(reason="Halt Boss and Scheduler", actor="operator")

    # BossBrain must raise EmergencyStopTriggered
    with pytest.raises(EmergencyStopTriggered):
        await boss.analyze_and_plan("Build microservice")

    with pytest.raises(EmergencyStopTriggered):
        await boss.plan_and_create_project("Build microservice", project_repo=p_repo, task_repo=t_repo)

    # Scheduler dispatch_ready_tasks returns empty list immediately
    dispatched = await scheduler.dispatch_ready_tasks("proj-any")
    assert dispatched == []


@pytest.mark.asyncio
async def test_kill_switch_resume_validates_auth_token(monkeypatch):
    """
    Validates that resume(auth_token) strictly authenticates against configured secrets:
    - Invalid tokens are rejected with SecurityViolationError.
    - Valid owner and security reviewer tokens are accepted.
    - Bearer prefix is stripped properly.
    """
    from neural.config import get_settings
    from neural.exceptions import SecurityViolationError

    owner_secret = "test-owner-secret-token-" + "x" * 32
    sec_secret = "test-sec-reviewer-token-" + "y" * 32
    monkeypatch.setenv("OWNER_API_TOKEN", owner_secret)
    monkeypatch.setenv("SECURITY_REVIEWER_API_TOKEN", sec_secret)
    get_settings.cache_clear()

    ks = get_kill_switch()

    # 1. Reject invalid token
    await ks.emergency_halt(reason="Token test 1", actor="admin")
    assert ks.is_halted is True

    with pytest.raises(SecurityViolationError):
        await ks.resume(actor="attacker", auth_token="forged-token-xyz")

    assert ks.is_halted is True

    # 2. Reject bearer-prefixed invalid token
    with pytest.raises(SecurityViolationError):
        await ks.resume(actor="attacker", auth_token="Bearer forged-token-xyz")

    assert ks.is_halted is True

    # 3. Accept valid owner token
    resume_owner = await ks.resume(actor="owner", auth_token=owner_secret)
    assert resume_owner["resumed"] is True
    assert ks.is_halted is False

    # 4. Accept valid security reviewer token with Bearer prefix
    await ks.emergency_halt(reason="Token test 2", actor="admin")
    assert ks.is_halted is True

    resume_sec = await ks.resume(actor="sec_reviewer", auth_token=f"Bearer {sec_secret}")
    assert resume_sec["resumed"] is True
    assert ks.is_halted is False

    get_settings.cache_clear()
