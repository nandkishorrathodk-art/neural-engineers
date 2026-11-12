"""Milestone A: stale-assignment auto-recovery and capability-aware routing.

Covers the P0 reliability fixes:
- a task pinned to an employee whose workstation is absent/failed is requeued;
- infrastructure recovery does not consume the task's code-failure retry budget;
- a task is never assigned to an employee lacking the required execution capability;
- a claim is refused when the assignee's desktop is not running.
"""

import pytest
from neural.constants import (
    ContainerStatus,
    EmployeeStatus,
    ExecutionMode,
    TaskStatus,
    required_execution_capabilities,
)
from neural.db.models import Container, Employee, EmployeeSkill, Task
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


def _task(task_id, *, employee_id, mode=ExecutionMode.TERMINAL, status=TaskStatus.ASSIGNED):
    return Task(
        task_id=task_id,
        project_id="PROJ-RECOVERY-1",
        title="Recovery Validation",
        objective="Validate stale assignment recovery",
        scope="Recovery",
        acceptance_criteria="Task is requeued",
        execution_mode=mode,
        status=status,
        assigned_to=employee_id,
        fencing_token=1,
        required_capabilities=["Python"],
    )


@pytest.mark.asyncio
async def test_failed_workstation_requeues_task_without_burning_retries(tmp_path):
    session_factory = await _build_session(tmp_path, "failed-desktop.db")

    async with session_factory() as setup:
        container = Container(
            docker_id="ancos-desktop-anc-emp-failed",
            image="ancos-desktop:latest",
            workspace_path="/home/employee/workspace",
            status=ContainerStatus.FAILED,
            vnc_port=5901,
            novnc_port=6081,
        )
        setup.add(container)
        await setup.flush()
        employee = Employee(
            employee_id="ANC-EMP-FAILED",
            name="Stranded Worker",
            title="Software Engineer",
            system_prompt="Test employee.",
            status=EmployeeStatus.WORKING,
            container_id=container.id,
            execution_capabilities=["terminal"],
        )
        setup.add(employee)
        await setup.flush()
        setup.add(EmployeeSkill(employee_id=employee.id, skill_name="Python", verified=True))
        setup.add(_task("TASK-RECOVER-1", employee_id=employee.id))
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered = await repo.recover_stale_assignments()
        assert [r["task_id"] for r in recovered] == ["TASK-RECOVER-1"]
        assert recovered[0]["previous_assignee"] == "ANC-EMP-FAILED"

        task = await repo.get_by_task_id("TASK-RECOVER-1")
        assert task.status == TaskStatus.PENDING
        assert task.assigned_to is None
        assert task.retry_count == 0, "infrastructure recovery must not consume retry budget"
        assert task.fencing_token == 2, "requeue must invalidate the prior fencing token"

        employee = await repo.get_by_employee_id("ANC-EMP-FAILED")
        assert employee.status == EmployeeStatus.IDLE


@pytest.mark.asyncio
async def test_capability_mismatch_requeues_task(tmp_path):
    session_factory = await _build_session(tmp_path, "capability.db")

    async with session_factory() as setup:
        container = Container(
            docker_id="ancos-desktop-anc-emp-terminal",
            image="ancos-desktop:latest",
            workspace_path="/home/employee/workspace",
            status=ContainerStatus.RUNNING,
            vnc_port=5902,
            novnc_port=6082,
        )
        setup.add(container)
        await setup.flush()
        employee = Employee(
            employee_id="ANC-EMP-TERMONLY",
            name="Terminal Only",
            title="Backend Engineer",
            system_prompt="Test employee.",
            status=EmployeeStatus.WORKING,
            container_id=container.id,
            execution_capabilities=["terminal"],
        )
        setup.add(employee)
        await setup.flush()
        setup.add(
            _task("TASK-RECOVER-2", employee_id=employee.id, mode=ExecutionMode.GUI_ONLY)
        )
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered = await repo.recover_stale_assignments()
        assert [r["task_id"] for r in recovered] == ["TASK-RECOVER-2"]
        task = await repo.get_by_task_id("TASK-RECOVER-2")
        assert task.status == TaskStatus.PENDING
        assert task.assigned_to is None


@pytest.mark.asyncio
async def test_live_assignment_is_not_recovered(tmp_path):
    session_factory = await _build_session(tmp_path, "live.db")

    async with session_factory() as setup:
        container = Container(
            docker_id="ancos-desktop-anc-emp-live",
            image="ancos-desktop:latest",
            workspace_path="/home/employee/workspace",
            status=ContainerStatus.RUNNING,
            vnc_port=5903,
            novnc_port=6083,
        )
        setup.add(container)
        await setup.flush()
        employee = Employee(
            employee_id="ANC-EMP-LIVE",
            name="Healthy Worker",
            title="Software Engineer",
            system_prompt="Test employee.",
            status=EmployeeStatus.WORKING,
            container_id=container.id,
            execution_capabilities=["terminal"],
        )
        setup.add(employee)
        await setup.flush()
        setup.add(EmployeeSkill(employee_id=employee.id, skill_name="Python", verified=True))
        setup.add(_task("TASK-LIVE-1", employee_id=employee.id))
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        recovered = await repo.recover_stale_assignments()
        assert recovered == []
        task = await repo.get_by_task_id("TASK-LIVE-1")
        assert task.status == TaskStatus.ASSIGNED
        assert task.assigned_to is not None


@pytest.mark.asyncio
async def test_claim_refused_when_workstation_not_running(tmp_path):
    session_factory = await _build_session(tmp_path, "claim.db")

    async with session_factory() as setup:
        container = Container(
            docker_id="ancos-desktop-anc-emp-claim",
            image="ancos-desktop:latest",
            workspace_path="/home/employee/workspace",
            status=ContainerStatus.STOPPED,
            vnc_port=5904,
            novnc_port=6084,
        )
        setup.add(container)
        await setup.flush()
        employee = Employee(
            employee_id="ANC-EMP-CLAIM",
            name="Stopped Desktop",
            title="Software Engineer",
            system_prompt="Test employee.",
            status=EmployeeStatus.IDLE,
            container_id=container.id,
            execution_capabilities=["terminal"],
        )
        setup.add(employee)
        await setup.flush()
        setup.add(EmployeeSkill(employee_id=employee.id, skill_name="Python", verified=True))
        setup.add(_task("TASK-CLAIM-1", employee_id=employee.id))
        await setup.commit()

    async with session_factory() as session:
        repo = TaskRepository(session)
        claim = await repo.claim_next_task("worker-1", execution_modes=["terminal"])
        assert claim is None


def test_required_capabilities_mapping():
    assert required_execution_capabilities(ExecutionMode.TERMINAL) == frozenset({"terminal"})
    assert required_execution_capabilities(ExecutionMode.GUI_ONLY) == frozenset({"gui"})
    assert required_execution_capabilities(ExecutionMode.HYBRID) == frozenset({"gui", "terminal"})
