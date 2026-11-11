import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from neural.constants import ContainerStatus, EmployeeStatus, ExecutionMode, TaskStatus
from neural.db.models import Container, Employee, EmployeeSkill, Task
from neural.db.repositories import TaskRepository


@pytest.mark.asyncio
async def test_concurrent_worker_claims_are_atomic(tmp_path):
    """
    Verifies that simultaneous worker claims on the same assigned task queue
    are strictly atomic: exactly one worker acquires the lease, while concurrent
    claims cannot acquire the same task.
    """
    from neural.db.engine import Base
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    database_path = tmp_path / "concurrent-claim.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path.as_posix()}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as s_init:
        container = Container(
            docker_id="ancos-desktop-anc-emp-worker1",
            image="ancos-desktop:latest",
            workspace_path="/home/employee/workspace",
            status=ContainerStatus.RUNNING,
            vnc_port=5900,
            novnc_port=6080,
        )
        s_init.add(container)
        await s_init.flush()

        employee = Employee(
            employee_id="ANC-EMP-WORKER1",
            name="Concurrent Worker 1",
            title="Software Engineer",
            system_prompt="Test employee system prompt.",
            status=EmployeeStatus.IDLE,
            container_id=container.id,
        )
        s_init.add(employee)
        await s_init.flush()

        skill = EmployeeSkill(
            employee_id=employee.id,
            skill_name="Python",
            verified=True,
        )
        s_init.add(skill)

        task = Task(
            task_id="TASK-CONCURRENT-01",
            project_id="PROJ-CONCURRENCY-1",
            title="Atomic Claim Validation",
            objective="Verify race-free worker claim",
            scope="Concurrency test",
            acceptance_criteria="Only one claim succeeds",
            execution_mode=ExecutionMode.TERMINAL,
            status=TaskStatus.ASSIGNED,
            assigned_to=employee.id,
            fencing_token=1,
            required_capabilities=["Python"],
        )
        s_init.add(task)
        await s_init.commit()

    async with session_factory() as s1, session_factory() as s2:
        repo1 = TaskRepository(s1)
        repo2 = TaskRepository(s2)

        # Launch two concurrent claims for worker-alpha and worker-beta
        res1, res2 = await asyncio.gather(
            repo1.claim_next_task(worker_id="worker-alpha", lease_seconds=60),
            repo2.claim_next_task(worker_id="worker-beta", lease_seconds=60),
        )

    claims = [r for r in [res1, res2] if r is not None]
    assert len(claims) == 1, f"Expected exactly 1 successful claim, got {len(claims)}"

    winner = claims[0]
    assert winner["task_id"] == "TASK-CONCURRENT-01"
    assert winner["fencing_token"] == 2
    assert winner["employee_id"] == "ANC-EMP-WORKER1"

    # Verify task state in database
    async with session_factory() as s3:
        refreshed_task = await TaskRepository(s3).get_by_task_id("TASK-CONCURRENT-01")
        assert refreshed_task.fencing_token == 2
        attempts = await TaskRepository(s3).list_attempts_for_task(refreshed_task.task_id)
        assert len(attempts) == 1
        assert attempts[0].status == "leased"
        assert attempts[0].fencing_token == 2


@pytest.mark.asyncio
async def test_claim_rejects_unqualified_skills(async_session):
    """
    Verifies that worker cannot claim task if assigned employee lacks required capabilities.
    """
    container = Container(
        docker_id="ancos-desktop-anc-emp-worker-noskill",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5901,
        novnc_port=6081,
    )
    async_session.add(container)
    await async_session.flush()

    employee = Employee(
        employee_id="ANC-EMP-NOSKILL",
        name="No Skill Worker",
        title="Frontend Intern",
        system_prompt="Test employee system prompt.",
        status=EmployeeStatus.IDLE,
        container_id=container.id,
    )
    async_session.add(employee)
    await async_session.flush()

    skill = EmployeeSkill(
        employee_id=employee.id,
        skill_name="CSS",
        verified=True,
    )
    async_session.add(skill)

    task = Task(
        task_id="TASK-SKILL-REQUIRED-01",
        project_id="PROJ-SKILL-1",
        title="Requires Rust",
        objective="Implement Rust systems component",
        scope="Rust module",
        acceptance_criteria="passes cargo test",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
        assigned_to=employee.id,
        fencing_token=1,
        required_capabilities=["Rust"],
    )
    async_session.add(task)
    await async_session.commit()

    repo = TaskRepository(async_session)
    claim = await repo.claim_next_task(worker_id="worker-rust-seeker")
    assert claim is None, "Worker should not claim task requiring Rust when employee only has CSS"


@pytest.mark.asyncio
async def test_claim_rejects_stopped_container(async_session):
    """
    Verifies that worker cannot claim task if employee workstation container is not RUNNING.
    """
    container = Container(
        docker_id="ancos-desktop-anc-emp-worker-stopped",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.STOPPED,
        vnc_port=5902,
        novnc_port=6082,
    )
    async_session.add(container)
    await async_session.flush()

    employee = Employee(
        employee_id="ANC-EMP-STOPPED",
        name="Stopped Worker",
        title="Engineer",
        system_prompt="Test employee system prompt.",
        status=EmployeeStatus.IDLE,
        container_id=container.id,
    )
    async_session.add(employee)
    await async_session.flush()

    skill = EmployeeSkill(
        employee_id=employee.id,
        skill_name="Python",
        verified=True,
    )
    async_session.add(skill)

    task = Task(
        task_id="TASK-CONTAINER-STOPPED-01",
        project_id="PROJ-CONTAINER-1",
        title="Requires Running Container",
        objective="Run python script",
        scope="Execution",
        acceptance_criteria="test pass",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
        assigned_to=employee.id,
        fencing_token=1,
        required_capabilities=["Python"],
    )
    async_session.add(task)
    await async_session.commit()

    repo = TaskRepository(async_session)
    claim = await repo.claim_next_task(worker_id="worker-alpha")
    assert claim is None, "Worker should not claim task when employee container is STOPPED"


@pytest.mark.asyncio
async def test_expired_lease_reassignment_with_incremented_fence(async_session):
    """
    Verifies that an expired worker lease is detected, marked failed/expired,
    and cleanly re-leased with monotonically incremented fencing token.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    past = now - timedelta(minutes=5)

    container = Container(
        docker_id="ancos-desktop-anc-emp-worker-expired",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5903,
        novnc_port=6083,
    )
    async_session.add(container)
    await async_session.flush()

    employee = Employee(
        employee_id="ANC-EMP-EXPIRED-TEST",
        name="Expired Lease Worker",
        title="Senior Engineer",
        system_prompt="Test employee system prompt.",
        status=EmployeeStatus.IDLE,
        container_id=container.id,
    )
    async_session.add(employee)
    await async_session.flush()

    skill = EmployeeSkill(
        employee_id=employee.id,
        skill_name="Python",
        verified=True,
    )
    async_session.add(skill)

    task = Task(
        task_id="TASK-EXPIRED-LEASE-01",
        project_id="PROJ-EXPIRE-1",
        title="Expired Lease Task",
        objective="Task whose first attempt lease expired",
        scope="Recovery test",
        acceptance_criteria="re-claimed with higher fence",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
        assigned_to=employee.id,
        fencing_token=5,
        required_capabilities=["Python"],
    )
    async_session.add(task)
    await async_session.commit()

    repo = TaskRepository(async_session)

    # First claim
    claim1 = await repo.claim_next_task(worker_id="worker-first", lease_seconds=1)
    assert claim1 is not None
    assert claim1["fencing_token"] == 6

    # Manually expire attempt lease in database
    attempts = await repo.list_attempts_for_task("TASK-EXPIRED-LEASE-01")
    assert len(attempts) == 1
    attempts[0].lease_expires_at = past
    employee.status = EmployeeStatus.IDLE
    await async_session.commit()

    # Next claim pass detects expired attempt, marks it failed, and applies retry backoff
    claim_sweep = await repo.claim_next_task(worker_id="worker-sweep", lease_seconds=60)
    assert claim_sweep is None

    # Verify attempt is now failed and task fencing_token was incremented
    attempts_after = await repo.list_attempts_for_task("TASK-EXPIRED-LEASE-01")
    assert attempts_after[0].status == "failed"
    task_after = await repo.get_by_task_id("TASK-EXPIRED-LEASE-01")
    assert task_after.fencing_token == 7

    # Fast forward past retry backoff window
    attempts_after[0].lease_expires_at = past
    await async_session.commit()

    # Now standby worker claims the task with fresh lease and higher fencing token
    claim2 = await repo.claim_next_task(worker_id="worker-second", lease_seconds=60)
    assert claim2 is not None
    assert claim2["fencing_token"] == 8
    assert claim2["attempt_number"] == 2
