"""NEURO-12C: assignment vs. stale-recovery provisioning race.

Observed live failure (hybrid-008): a GUI/hybrid task was assigned, then the
stale-recovery loop requeued it to PENDING ~0.5s later while its desktop was
still provisioning. The desktop came up, but nothing re-dispatched the PENDING
task, so it starved until timeout.

These tests lock in the two halves of the fix:

1. Provisioning time is not treated as worker lease expiry: a recently assigned
   task whose container is not yet running is left ASSIGNED, while a genuinely
   dead assignment is still recovered.
2. A PENDING task is re-dispatched once a qualified, running desktop exists, and
   only to employees capable of the task's mode.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from neural.constants import ContainerStatus, EmployeeStatus, ExecutionMode, TaskStatus
from neural.db.models import Container, Employee, EmployeeSkill, ResourceLease, Task, TaskDependency
from neural.db.repositories import TaskRepository, utc_now

_ENGINES: list = []


async def _session_factory(tmp_path, name):
    from neural.db.engine import Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    _ENGINES.append(engine)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _dispose_test_engines():
    yield
    while _ENGINES:
        await _ENGINES.pop().dispose()


async def _add_employee(session, *, employee_id, capabilities, container_status, skills=("gui",)):
    container = Container(
        docker_id=f"ancos-desktop-{employee_id.lower()}",
        image="ancos-desktop:latest",
        workspace_path=f"/home/employee/{employee_id}",
        status=container_status,
        vnc_port=5901,
        novnc_port=6081,
    )
    session.add(container)
    await session.flush()
    employee = Employee(
        employee_id=employee_id,
        name="Race Employee",
        title="Software Engineer",
        system_prompt="Test employee.",
        status=EmployeeStatus.IDLE,
        container_id=container.id,
        execution_capabilities=list(capabilities),
    )
    session.add(employee)
    await session.flush()
    for skill in skills:
        session.add(EmployeeSkill(employee_id=employee.id, skill_name=skill, verified=True))
    await session.flush()
    return employee, container


def _task(task_id, *, employee_id, mode=ExecutionMode.HYBRID, status=TaskStatus.ASSIGNED, assigned_at=None):
    return Task(
        task_id=task_id,
        project_id="PROJ-RACE-1",
        title="Race Validation",
        objective="Validate the assignment/provisioning race",
        scope="Race",
        acceptance_criteria="Task is not stranded",
        execution_mode=mode,
        status=status,
        assigned_to=employee_id,
        assigned_at=assigned_at,
        fencing_token=1,
        required_capabilities=["gui"],
    )


@pytest.mark.asyncio
async def test_recently_assigned_task_awaiting_provisioning_is_not_requeued(tmp_path):
    """The hybrid-008 failure: recovery must not fire while the desktop provisions."""
    session_factory = await _session_factory(tmp_path, "provisioning-race.db")
    now = utc_now()
    async with session_factory() as setup:
        employee, container = await _add_employee(
            setup,
            employee_id="ANC-EMP-RACE-PROVISIONING",
            capabilities=["terminal", "gui"],
            container_status=ContainerStatus.TERMINATED,
        )
        setup.add(
            _task(
                "TASK-RACE-PROVISIONING",
                employee_id=employee.id,
                status=TaskStatus.ASSIGNED,
                assigned_at=now,
            )
        )
        await setup.commit()
        employee_id = employee.id
        container_id = container.id

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered = await repo.recover_stale_assignments(now=now + timedelta(seconds=1))
        assert recovered == [], "a task whose desktop is still provisioning must not be requeued"
        task = await repo.get_by_task_id("TASK-RACE-PROVISIONING")
        assert task.status == TaskStatus.ASSIGNED
        assert task.assigned_to == employee_id
        assert task.fencing_token == 1, "an untouched assignment must keep its fencing token"

        # Desktop becomes ready: the same task is still ASSIGNED and claimable.
        container = await session.get(Container, container_id)
        container.status = ContainerStatus.RUNNING
        await session.commit()
        recovered = await repo.recover_stale_assignments(now=now + timedelta(seconds=2))
        assert recovered == []
        task = await repo.get_by_task_id("TASK-RACE-PROVISIONING")
        assert task.status == TaskStatus.ASSIGNED


@pytest.mark.asyncio
async def test_genuinely_dead_desktop_is_still_recovered_after_grace(tmp_path):
    session_factory = await _session_factory(tmp_path, "dead-desktop.db")
    now = utc_now()
    async with session_factory() as setup:
        employee, _ = await _add_employee(
            setup,
            employee_id="ANC-EMP-RACE-DEAD",
            capabilities=["terminal", "gui"],
            container_status=ContainerStatus.FAILED,
        )
        setup.add(
            _task(
                "TASK-RACE-DEAD",
                employee_id=employee.id,
                status=TaskStatus.ASSIGNED,
                assigned_at=now - timedelta(seconds=900),
            )
        )
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered = await repo.recover_stale_assignments(now=now)
        assert [r["task_id"] for r in recovered] == ["TASK-RACE-DEAD"]
        task = await repo.get_by_task_id("TASK-RACE-DEAD")
        assert task.status == TaskStatus.PENDING
        assert task.assigned_to is None
        assert task.assigned_at is None


@pytest.mark.asyncio
async def test_recent_lease_suppresses_recovery_for_old_assignment(tmp_path):
    """A provisioning lease alone excuses a missing desktop inside the window."""
    session_factory = await _session_factory(tmp_path, "recent-lease.db")
    now = utc_now()
    async with session_factory() as setup:
        employee, _ = await _add_employee(
            setup,
            employee_id="ANC-EMP-RACE-LEASE",
            capabilities=["terminal", "gui"],
            container_status=ContainerStatus.TERMINATED,
        )
        setup.add(
            _task(
                "TASK-RACE-LEASE",
                employee_id=employee.id,
                status=TaskStatus.ASSIGNED,
                assigned_at=now - timedelta(seconds=600),
            )
        )
        setup.add(
            ResourceLease(
                resource_key="desktop:local:slot-1",
                employee_id=employee.employee_id,
                session_id=f"ancos-desktop-{employee.employee_id.lower()}",
                fencing_token=7,
                status="active",
                granted_at=now - timedelta(seconds=5),
                heartbeat_at=now - timedelta(seconds=5),
                lease_expires_at=now + timedelta(seconds=3600),
            )
        )
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered = await repo.recover_stale_assignments(now=now)
        assert recovered == []
        task = await repo.get_by_task_id("TASK-RACE-LEASE")
        assert task.status == TaskStatus.ASSIGNED


@pytest.mark.asyncio
async def test_capability_mismatch_is_recovered_even_when_recent(tmp_path):
    """A permanent defect must never be excused by the provisioning window."""
    session_factory = await _session_factory(tmp_path, "capability-mismatch.db")
    now = utc_now()
    async with session_factory() as setup:
        employee, _ = await _add_employee(
            setup,
            employee_id="ANC-EMP-RACE-TERMONLY",
            capabilities=["terminal"],
            container_status=ContainerStatus.RUNNING,
        )
        setup.add(
            _task(
                "TASK-RACE-CAP",
                employee_id=employee.id,
                mode=ExecutionMode.GUI_ONLY,
                status=TaskStatus.ASSIGNED,
                assigned_at=now,
            )
        )
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered = await repo.recover_stale_assignments(now=now)
        assert [r["task_id"] for r in recovered] == ["TASK-RACE-CAP"]
        assert recovered[0]["reason"] == "assignee lacks required execution capability"


@pytest.mark.asyncio
async def test_zero_grace_disables_the_provisioning_excuse(tmp_path, monkeypatch):
    monkeypatch.setenv("ANCOS_DESKTOP_PROVISIONING_GRACE_SECONDS", "0")
    session_factory = await _session_factory(tmp_path, "zero-grace.db")
    now = utc_now()
    async with session_factory() as setup:
        employee, _ = await _add_employee(
            setup,
            employee_id="ANC-EMP-RACE-ZEROG",
            capabilities=["terminal", "gui"],
            container_status=ContainerStatus.TERMINATED,
        )
        setup.add(
            _task(
                "TASK-RACE-ZEROG",
                employee_id=employee.id,
                status=TaskStatus.ASSIGNED,
                assigned_at=now,
            )
        )
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered = await repo.recover_stale_assignments(now=now)
        assert [r["task_id"] for r in recovered] == ["TASK-RACE-ZEROG"]


@pytest.mark.asyncio
async def test_pending_task_is_redispatched_to_capable_running_employee(tmp_path):
    """After a requeue, a ready desktop must pull the PENDING task back."""
    session_factory = await _session_factory(tmp_path, "redispatch.db")
    async with session_factory() as setup:
        employee, _ = await _add_employee(
            setup,
            employee_id="ANC-EMP-RACE-READY",
            capabilities=["terminal", "gui"],
            container_status=ContainerStatus.RUNNING,
        )
        setup.add(
            Task(
                task_id="TASK-RACE-PENDING",
                project_id="PROJ-RACE-1",
                title="Requeued work",
                objective="Get re-dispatched",
                scope="Race",
                acceptance_criteria="Task is assigned",
                execution_mode=ExecutionMode.HYBRID,
                status=TaskStatus.PENDING,
                assigned_to=None,
                fencing_token=2,
                required_capabilities=["gui"],
            )
        )
        await setup.commit()
        employee_id = employee.id

    async with session_factory() as session:
        repo = TaskRepository(session)
        assigned = await repo.redispatch_pending_tasks(["gui", "hybrid"])
        assert assigned == ["TASK-RACE-PENDING"]
        task = await repo.get_by_task_id("TASK-RACE-PENDING")
        assert task.status == TaskStatus.ASSIGNED
        assert task.assigned_to == employee_id
        assert task.assigned_at is not None
        assert task.fencing_token == 3


@pytest.mark.asyncio
async def test_pending_gui_task_is_not_redispatched_to_terminal_only_employee(tmp_path):
    session_factory = await _session_factory(tmp_path, "redispatch-capability.db")
    async with session_factory() as setup:
        await _add_employee(
            setup,
            employee_id="ANC-EMP-RACE-TERMONLY2",
            capabilities=["terminal"],
            container_status=ContainerStatus.RUNNING,
        )
        setup.add(
            Task(
                task_id="TASK-RACE-PENDING2",
                project_id="PROJ-RACE-1",
                title="GUI work",
                objective="Needs a desktop",
                scope="Race",
                acceptance_criteria="Task is assigned",
                execution_mode=ExecutionMode.GUI_ONLY,
                status=TaskStatus.PENDING,
                assigned_to=None,
                fencing_token=0,
                required_capabilities=["gui"],
            )
        )
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        assigned = await repo.redispatch_pending_tasks(["gui"])
        assert assigned == []
        task = await repo.get_by_task_id("TASK-RACE-PENDING2")
        assert task.status == TaskStatus.PENDING
        assert task.assigned_to is None


@pytest.mark.asyncio
async def test_pending_task_with_unmet_dependency_is_not_redispatched(tmp_path):
    session_factory = await _session_factory(tmp_path, "redispatch-dependency.db")
    async with session_factory() as setup:
        await _add_employee(
            setup,
            employee_id="ANC-EMP-RACE-DEP",
            capabilities=["terminal", "gui"],
            container_status=ContainerStatus.RUNNING,
        )
        prerequisite = Task(
            task_id="TASK-RACE-PREREQ",
            project_id="PROJ-RACE-1",
            title="Prerequisite",
            objective="Must finish first",
            scope="Race",
            acceptance_criteria="Done",
            execution_mode=ExecutionMode.HYBRID,
            status=TaskStatus.RUNNING,
            assigned_to=None,
            fencing_token=0,
            required_capabilities=["gui"],
        )
        dependent = Task(
            task_id="TASK-RACE-DEPENDENT",
            project_id="PROJ-RACE-1",
            title="Dependent work",
            objective="Waits for prereq",
            scope="Race",
            acceptance_criteria="Task is assigned",
            execution_mode=ExecutionMode.HYBRID,
            status=TaskStatus.PENDING,
            assigned_to=None,
            fencing_token=0,
            required_capabilities=["gui"],
        )
        setup.add(prerequisite)
        setup.add(dependent)
        await setup.flush()
        # TaskDependency references primary keys, not the public task_id.
        setup.add(
            TaskDependency(task_id=dependent.id, depends_on_task_id=prerequisite.id)
        )
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        assigned = await repo.redispatch_pending_tasks(["gui", "hybrid"])
        assert assigned == []
        task = await repo.get_by_task_id("TASK-RACE-DEPENDENT")
        assert task.status == TaskStatus.PENDING
        assert task.assigned_to is None


@pytest.mark.asyncio
async def test_recovery_loop_redispatches_pending_task_after_recovery(tmp_path):
    """End-to-end: a stranded task is recovered and then re-dispatched in one cycle."""
    from neural.tasks.recovery_loop import run_stale_recovery_cycle

    session_factory = await _session_factory(tmp_path, "recovery-loop.db")
    now = utc_now()
    async with session_factory() as setup:
        # The stranded task's own employee has a dead desktop.
        stranded, _ = await _add_employee(
            setup,
            employee_id="ANC-EMP-LOOP-STRANDED",
            capabilities=["terminal", "gui"],
            container_status=ContainerStatus.FAILED,
        )
        setup.add(
            _task(
                "TASK-LOOP-STRANDED",
                employee_id=stranded.id,
                status=TaskStatus.ASSIGNED,
                assigned_at=now - timedelta(seconds=900),
            )
        )
        # A second, healthy employee with a running desktop can take it over.
        ready, _ = await _add_employee(
            setup,
            employee_id="ANC-EMP-LOOP-READY",
            capabilities=["terminal", "gui"],
            container_status=ContainerStatus.RUNNING,
        )
        await setup.commit()
        stranded_id = stranded.id
        ready_employee_id = ready.id

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered, redispatched = await run_stale_recovery_cycle(repo)
        assert [r["task_id"] for r in recovered] == ["TASK-LOOP-STRANDED"]
        assert redispatched == ["TASK-LOOP-STRANDED"]

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered_task = await repo.get_by_task_id("TASK-LOOP-STRANDED")
        # Recovered from the dead assignee and re-dispatched to the healthy one,
        # all within a single recovery cycle.
        assert recovered_task.status == TaskStatus.ASSIGNED
        assert recovered_task.assigned_to == ready_employee_id
        assert recovered_task.assigned_to != stranded_id
        assert recovered_task.assigned_at is not None
