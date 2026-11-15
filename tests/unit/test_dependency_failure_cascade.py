"""Dependents of a failed task must be blocked, never left PENDING forever.

A worker-reported failure, a failed independent verification, or an expired
lease all terminate a task outside the in-process DAG runner. Before this
cascade existed, downstream tasks stayed PENDING indefinitely because their
prerequisite could never become COMPLETED: the project silently stalled instead
of reporting a truthful terminal state.
"""

from datetime import datetime, timedelta, timezone

import pytest
from neural.constants import (
    ContainerStatus,
    EmployeeStatus,
    ExecutionMode,
    TaskStatus,
)
from neural.db.models import Container, Employee, EmployeeSkill, Task, TaskAttempt
from neural.db.repositories import TaskRepository


async def _build_session(tmp_path, name):
    from neural.db.engine import Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _task(
    task_id,
    *,
    status,
    employee_id=None,
    mode=ExecutionMode.TERMINAL,
    project="PROJ-CASCADE-1",
    retry_count=0,
    max_retries=3,
):
    return Task(
        task_id=task_id,
        project_id=project,
        title=f"Task {task_id}",
        objective="Validate dependency failure cascade",
        scope="Cascade",
        acceptance_criteria="Dependents are blocked",
        execution_mode=mode,
        status=status,
        assigned_to=employee_id,
        fencing_token=1,
        retry_count=retry_count,
        max_retries=max_retries,
        required_capabilities=["Python"],
    )


async def _seed_employee(session, *, employee_id, suffix):
    container = Container(
        docker_id=f"ancos-desktop-anc-emp-{suffix}",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5910,
        novnc_port=6090,
    )
    session.add(container)
    await session.flush()
    employee = Employee(
        employee_id=employee_id,
        name=f"Worker {suffix}",
        title="Software Engineer",
        system_prompt="Test employee.",
        status=EmployeeStatus.WORKING,
        container_id=container.id,
        execution_capabilities=["terminal"],
    )
    session.add(employee)
    await session.flush()
    session.add(EmployeeSkill(employee_id=employee.id, skill_name="Python", verified=True))
    return employee


@pytest.mark.asyncio
async def test_worker_failure_blocks_transitive_dependents(tmp_path):
    """A failed TASK-01 must block TASK-02, which must block TASK-03."""
    session_factory = await _build_session(tmp_path, "cascade-worker.db")

    async with session_factory() as setup:
        employee = await _seed_employee(setup, employee_id="ANC-EMP-CASCADE", suffix="cascade")
        first = _task(
            "TASK-01",
            status=TaskStatus.RUNNING,
            employee_id=employee.id,
            retry_count=3,
            max_retries=3,
        )
        second = _task("TASK-02", status=TaskStatus.PENDING)
        third = _task("TASK-03", status=TaskStatus.PENDING)
        setup.add_all([first, second, third])
        await setup.flush()
        setup.add(
            TaskAttempt(
                task_id=first.id,
                attempt_number=1,
                worker_id="worker-cascade",
                fencing_token=first.fencing_token,
                status="running",
                lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(seconds=60),
            )
        )
        from neural.db.models import TaskDependency

        setup.add(TaskDependency(task_id=second.id, depends_on_task_id=first.id))
        setup.add(TaskDependency(task_id=third.id, depends_on_task_id=second.id))
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        result = await repo.fail_task_attempt(
            task_id="TASK-01",
            worker_id="worker-cascade",
            fencing_token=1,
            error_message="Terminal command exited with code 1: boom",
            exit_code=1,
        )
        assert result is not None
        assert result["retrying"] is False

        assert (await repo.get_by_task_id("TASK-01")).status == TaskStatus.FAILED
        assert (await repo.get_by_task_id("TASK-02")).status == TaskStatus.BLOCKED
        assert (await repo.get_by_task_id("TASK-03")).status == TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_retryable_failure_does_not_block_dependents(tmp_path):
    """A retryable failure keeps the task runnable, so dependents stay PENDING."""
    session_factory = await _build_session(tmp_path, "cascade-retry.db")

    async with session_factory() as setup:
        employee = await _seed_employee(setup, employee_id="ANC-EMP-RETRY", suffix="retry")
        first = _task("TASK-01", status=TaskStatus.RUNNING, employee_id=employee.id)
        second = _task("TASK-02", status=TaskStatus.PENDING)
        setup.add_all([first, second])
        await setup.flush()
        setup.add(
            TaskAttempt(
                task_id=first.id,
                attempt_number=1,
                worker_id="worker-retry",
                fencing_token=first.fencing_token,
                status="running",
                lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
                + timedelta(seconds=60),
            )
        )
        from neural.db.models import TaskDependency

        setup.add(TaskDependency(task_id=second.id, depends_on_task_id=first.id))
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        result = await repo.fail_task_attempt(
            task_id="TASK-01",
            worker_id="worker-retry",
            fencing_token=1,
            error_message="Worker crashed while running the planned command.",
        )
        assert result is not None
        assert result["retrying"] is True
        assert (await repo.get_by_task_id("TASK-02")).status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_heal_orphaned_dependents_blocks_stalled_chain(tmp_path):
    """Healing blocks dependents a prerequisite failed on before the cascade existed."""
    session_factory = await _build_session(tmp_path, "cascade-heal.db")

    async with session_factory() as setup:
        first = _task("TASK-01", status=TaskStatus.FAILED)
        second = _task("TASK-02", status=TaskStatus.PENDING)
        third = _task("TASK-03", status=TaskStatus.PENDING)
        completed = _task("TASK-04", status=TaskStatus.COMPLETED)
        setup.add_all([first, second, third, completed])
        await setup.flush()
        from neural.db.models import TaskDependency

        setup.add(TaskDependency(task_id=second.id, depends_on_task_id=first.id))
        setup.add(TaskDependency(task_id=third.id, depends_on_task_id=second.id))
        # A dependent of a COMPLETED prerequisite must never be touched.
        setup.add(TaskDependency(task_id=third.id, depends_on_task_id=completed.id))
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        healed = await repo.heal_orphaned_dependents()
        assert set(healed) == {"TASK-02", "TASK-03"}
        assert (await repo.get_by_task_id("TASK-02")).status == TaskStatus.BLOCKED
        assert (await repo.get_by_task_id("TASK-03")).status == TaskStatus.BLOCKED

        # Idempotent: a second pass heals nothing new.
        assert await repo.heal_orphaned_dependents() == []
