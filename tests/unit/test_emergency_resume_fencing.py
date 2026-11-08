from datetime import timedelta

import pytest
from neural.constants import TaskStatus
from neural.db.models import TaskAttempt
from neural.db.repositories import ProjectRepository, TaskRepository, utc_now
from neural.security.kill_switch import GlobalEmergencyKillSwitch
from sqlalchemy import select


@pytest.mark.asyncio
async def test_emergency_resume_cancels_attempt_and_supersedes_worker_fence(async_session):
    project = await ProjectRepository(async_session).create(
        name="Halt recovery fencing",
        objective="Requeue work without reviving a pre-halt worker lease",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-HALT-RECOVERY-1",
        project_id=project.id,
        title="Recover interrupted task",
        objective="Resume safely after emergency stop",
        scope="One running attempt",
        acceptance_criteria="The old worker lease is invalid",
        status=TaskStatus.RUNNING,
        fencing_token=7,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-before-halt",
        fencing_token=7,
        status="running",
        claimed_at=utc_now() - timedelta(seconds=20),
        started_at=utc_now() - timedelta(seconds=19),
        lease_expires_at=utc_now() + timedelta(minutes=1),
    )
    async_session.add(attempt)
    await async_session.commit()

    kill_switch = GlobalEmergencyKillSwitch()
    await kill_switch.emergency_halt(reason="contain active execution")
    receipt = await kill_switch.resume(task_repo=TaskRepository(async_session))

    recovered = await TaskRepository(async_session).get_by_task_id(task.task_id)
    stored_attempt = await async_session.scalar(
        select(TaskAttempt).where(TaskAttempt.id == attempt.id)
    )
    assert receipt["recovered_tasks_count"] == 1
    assert recovered.status == TaskStatus.PENDING
    assert recovered.fencing_token == 8
    assert recovered.started_at is None
    assert stored_attempt.status == "cancelled"
    assert stored_attempt.lease_expires_at <= utc_now()
    assert "superseded" in stored_attempt.error_message


@pytest.mark.asyncio
async def test_resume_stays_halted_when_attempt_recovery_fails():
    class DurableState:
        def __init__(self):
            self.active = True

        async def get_state(self):
            return {
                "active": self.active,
                "reason": "test halt",
                "changed_by": "owner",
                "changed_at": None,
            }

        async def set_state(self, *, active, actor, reason):
            self.active = active

    class BrokenTaskRepository:
        async def recover_running_after_emergency_stop(self):
            raise RuntimeError("attempt table unavailable")

    state = DurableState()
    kill_switch = GlobalEmergencyKillSwitch()

    with pytest.raises(RuntimeError, match="attempt table unavailable"):
        await kill_switch.resume(task_repo=BrokenTaskRepository(), state_repo=state)

    assert state.active is True
    assert kill_switch.is_halted is True
