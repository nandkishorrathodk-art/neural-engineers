import asyncio

import pytest
from neural.boss.recovery import FailureRecoveryManager
from neural.constants import EmployeeStatus, TaskStatus
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository
from neural.exceptions import InvalidTaskStateTransitionError, TaskDependencyError
from neural.evaluation.verifier import IndependentVerifier
from neural.tasks.dependencies import TaskGraph
from neural.tasks.scheduler import TaskScheduler
from neural.tasks.state_machine import TaskStateMachine
from sqlalchemy.ext.asyncio import AsyncSession


class DummyTask:
    def __init__(self, task_id: str = "TASK-001", status: TaskStatus = TaskStatus.PENDING):
        self.task_id = task_id
        self.status = status
        self.started_at = None
        self.completed_at = None
        self.retry_count = 0


# =========================================================================
# 1. Task State Machine Tests
# =========================================================================

def test_state_machine_valid_transitions():
    """Verify all legal lifecycle transitions across task states."""
    task = DummyTask(status=TaskStatus.PENDING)

    # PENDING -> ASSIGNED
    TaskStateMachine.transition(task, TaskStatus.ASSIGNED)
    assert task.status == TaskStatus.ASSIGNED

    # ASSIGNED -> RUNNING
    TaskStateMachine.transition(task, TaskStatus.RUNNING)
    assert task.status == TaskStatus.RUNNING
    assert task.started_at is not None

    # RUNNING -> RETRYING
    TaskStateMachine.transition(task, TaskStatus.RETRYING)
    assert task.status == TaskStatus.RETRYING
    assert task.retry_count == 1

    # RETRYING -> RUNNING
    TaskStateMachine.transition(task, TaskStatus.RUNNING)
    assert task.status == TaskStatus.RUNNING

    # RUNNING -> COMPLETED (terminal)
    TaskStateMachine.transition(task, TaskStatus.COMPLETED)
    assert task.status == TaskStatus.COMPLETED
    assert task.completed_at is not None


def test_state_machine_failure_and_retry_recovery():
    """Verify RUNNING -> FAILED -> RETRYING -> PENDING -> RUNNING recovery path."""
    task = DummyTask(status=TaskStatus.RUNNING)

    TaskStateMachine.transition(task, TaskStatus.FAILED)
    assert task.status == TaskStatus.FAILED

    TaskStateMachine.transition(task, TaskStatus.RETRYING)
    assert task.status == TaskStatus.RETRYING
    assert task.retry_count == 1

    TaskStateMachine.transition(task, TaskStatus.PENDING)
    assert task.status == TaskStatus.PENDING


def test_state_machine_blocked_path():
    """Verify PENDING -> BLOCKED -> PENDING when dependencies stall and resolve."""
    task = DummyTask(status=TaskStatus.PENDING)

    TaskStateMachine.transition(task, TaskStatus.BLOCKED)
    assert task.status == TaskStatus.BLOCKED

    TaskStateMachine.transition(task, TaskStatus.PENDING)
    assert task.status == TaskStatus.PENDING


def test_state_machine_rejects_illegal_transitions():
    """Verify illegal transitions raise InvalidTaskStateTransitionError."""
    # Cannot jump directly from PENDING to COMPLETED (strict gate)
    pending_task = DummyTask(status=TaskStatus.PENDING)
    with pytest.raises(InvalidTaskStateTransitionError):
        TaskStateMachine.transition(pending_task, TaskStatus.COMPLETED)

    # Cannot transition out of terminal COMPLETED
    completed_task = DummyTask(status=TaskStatus.COMPLETED)
    with pytest.raises(InvalidTaskStateTransitionError):
        TaskStateMachine.transition(completed_task, TaskStatus.RUNNING)
    with pytest.raises(InvalidTaskStateTransitionError):
        TaskStateMachine.transition(completed_task, TaskStatus.PENDING)

    # Cannot transition out of terminal CANCELLED
    cancelled_task = DummyTask(status=TaskStatus.CANCELLED)
    with pytest.raises(InvalidTaskStateTransitionError):
        TaskStateMachine.transition(cancelled_task, TaskStatus.RUNNING)


def test_state_machine_idempotent_self_transition():
    """Transition to the exact same state is an idempotent no-op."""
    task = DummyTask(status=TaskStatus.RUNNING)
    res = TaskStateMachine.transition(task, TaskStatus.RUNNING)
    assert res == TaskStatus.RUNNING
    assert task.status == TaskStatus.RUNNING


def test_state_machine_terminal_check_and_allowed_list():
    """Verify terminal state detection and allowed targets helper."""
    assert TaskStateMachine.is_terminal(TaskStatus.COMPLETED) is True
    assert TaskStateMachine.is_terminal(TaskStatus.CANCELLED) is True
    assert TaskStateMachine.is_terminal(TaskStatus.RUNNING) is False
    assert TaskStateMachine.is_terminal(TaskStatus.PENDING) is False

    allowed = TaskStateMachine.get_allowed_transitions(TaskStatus.PENDING)
    allowed_values = {s.value for s in allowed}
    assert "assigned" in allowed_values
    assert "running" in allowed_values
    assert "blocked" in allowed_values
    assert "cancelled" in allowed_values
    assert "completed" not in allowed_values


# =========================================================================
# 2. TaskGraph & DAG Dependency Resolution Tests
# =========================================================================

def test_task_graph_complex_diamond_resolution():
    r"""
    Diamond DAG:
        A
       / \
      B   C
       \ /
        D
    A must precede B and C; both B and C must precede D.
    """
    tg = TaskGraph()
    tg.add_task("A", title="Architecture")
    tg.add_task("B", title="Backend API")
    tg.add_task("C", title="Frontend UI")
    tg.add_task("D", title="Integration Testing")

    tg.add_dependency("B", "A")
    tg.add_dependency("C", "A")
    tg.add_dependency("D", "B")
    tg.add_dependency("D", "C")

    topo = tg.topological_sort()
    assert topo.index("A") < topo.index("B")
    assert topo.index("A") < topo.index("C")
    assert topo.index("B") < topo.index("D")
    assert topo.index("C") < topo.index("D")

    # Critical path should have length 2 edges / 3 nodes (A -> B -> D or A -> C -> D)
    crit = tg.critical_path()
    assert len(crit) == 3
    assert crit[0] == "A"
    assert crit[-1] == "D"


    # Ready tasks progression
    ready_0 = tg.get_ready_tasks(completed_task_ids=set())
    assert ready_0 == ["A"]

    ready_1 = tg.get_ready_tasks(completed_task_ids={"A"})
    assert set(ready_1) == {"B", "C"}

    ready_2 = tg.get_ready_tasks(completed_task_ids={"A", "B"})
    assert ready_2 == ["C"]  # D not ready until C also completes!

    ready_3 = tg.get_ready_tasks(completed_task_ids={"A", "B", "C"})
    assert ready_3 == ["D"]


def test_task_graph_cycle_detection_varieties():
    """Detect self-cycles, 2-node cycles, and deep 4-node cycles."""
    tg = TaskGraph()
    tg.add_task("T1")
    tg.add_task("T2")
    tg.add_task("T3")
    tg.add_task("T4")

    # Self-dependency
    with pytest.raises(TaskDependencyError):
        tg.add_dependency("T1", "T1")

    # 2-node cycle
    tg.add_dependency("T2", "T1")
    with pytest.raises(TaskDependencyError):
        tg.add_dependency("T1", "T2")

    # Deep cycle: T1 -> T2 -> T3 -> T4 -> T1
    tg.add_dependency("T3", "T2")
    tg.add_dependency("T4", "T3")
    with pytest.raises(TaskDependencyError):
        tg.add_dependency("T1", "T4")


# =========================================================================
# 3. Scheduler Async Execution & Retry Flow Tests
# =========================================================================

@pytest.mark.asyncio
async def test_scheduler_parallel_branches_and_retries(async_session: AsyncSession, tmp_path):
    """
    Executes a multi-branch project with one flaky task that retries with backoff
    and transitions through TaskStatus.RETRYING before succeeding.
    """
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)

    project = await proj_repo.create(name="Project-P2-DAG", objective="Multi-branch execution")

    # Create tasks: Root -> Branch1 & Branch2 -> Join
    t_root = await task_repo.create(
        task_id="T-ROOT",
        project_id=project.id,
        title="Root Initialization",
        objective="Initialize DB schema",
        scope="Core",
        acceptance_criteria="Schema migrated",
        status=TaskStatus.PENDING
    )
    t_b1 = await task_repo.create(
        task_id="T-B1",
        project_id=project.id,
        title="Branch 1 Worker",
        objective="Build Auth",
        scope="Backend",
        acceptance_criteria="Auth ready",
        status=TaskStatus.PENDING
    )
    t_b2 = await task_repo.create(
        task_id="T-B2",
        project_id=project.id,
        title="Branch 2 Worker",
        objective="Build Dashboard",
        scope="Frontend",
        acceptance_criteria="Dashboard ready",
        status=TaskStatus.PENDING
    )
    t_join = await task_repo.create(
        task_id="T-JOIN",
        project_id=project.id,
        title="Join Task",
        objective="End-to-End Verification",
        scope="QA",
        acceptance_criteria="All green",
        status=TaskStatus.PENDING
    )

    # Wire dependencies
    await task_repo.add_dependency(t_b1.task_id, t_root.task_id)
    await task_repo.add_dependency(t_b2.task_id, t_root.task_id)
    await task_repo.add_dependency(t_join.task_id, t_b1.task_id)
    await task_repo.add_dependency(t_join.task_id, t_b2.task_id)

    recovery = FailureRecoveryManager(max_retries=2, initial_delay=0.01)
    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=emp_repo,
        max_concurrency=4,
        recovery_manager=recovery,
        verifier=IndependentVerifier(),
    )

    call_counts = {"T-B1": 0}

    async def worker_with_retry(task):
        if task.task_id == "T-B1":
            call_counts["T-B1"] += 1
            if call_counts["T-B1"] == 1:
                # First run fails with retryable transient error
                return {"status": "failure", "error": "Transient connection timeout 503"}
        await asyncio.sleep(0.01)
        artifact = tmp_path / f"{task.task_id}.txt"
        artifact.write_text(f"Deliverable for {task.task_id}", encoding="utf-8")
        return {"status": "success", "task_id": task.task_id, "artifacts": [str(artifact)]}

    result = await scheduler.execute_dag(project.id, worker_func=worker_with_retry, poll_interval=0.01)

    assert result["success"] is True
    assert len(result["completed"]) == 4
    assert len(result["failed"]) == 0
    assert len(result["blocked"]) == 0
    assert call_counts["T-B1"] == 2  # Proves auto-retry occurred!


def test_task_graph_cycle_error_details():
    """Verify that TaskDependencyError message contains the specific cycle path."""
    tg = TaskGraph()
    tg.add_task("N1")
    tg.add_task("N2")
    tg.add_task("N3")
    tg.add_dependency("N2", "N1")
    tg.add_dependency("N3", "N2")

    with pytest.raises(TaskDependencyError) as exc_info:
        tg.add_dependency("N1", "N3")

    msg = str(exc_info.value)
    assert "introduces a cycle" in msg
    assert "N1" in msg and "N2" in msg and "N3" in msg


@pytest.mark.asyncio
async def test_scheduler_resets_employee_to_idle_on_completion(async_session: AsyncSession, tmp_path):
    """
    Verify that an employee assigned to a task is automatically returned
    to EmployeeStatus.IDLE when the task finishes execution.
    """
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)

    from neural.employees.factory import EmployeeFactory
    factory = EmployeeFactory(emp_repo)
    emp = await factory.hire_employee(name="Worker Bee", title="Software Engineer")

    project = await proj_repo.create(name="Project-Idle-Reset", objective="Test status reset")
    await task_repo.create(
        task_id="T-IDLE-01",
        project_id=project.id,
        title="Quick Task",
        objective="Do work",
        scope="Core",
        acceptance_criteria="Pass",
        status=TaskStatus.PENDING
    )

    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=emp_repo,
        max_concurrency=2,
        verifier=IndependentVerifier(),
    )

    artifact = tmp_path / "employee-result.txt"

    async def worker(task):
        artifact.write_text("Verified lifecycle fixture", encoding="utf-8")
        return {"status": "success", "artifacts": [str(artifact)]}

    result = await scheduler.execute_dag(project.id, worker_func=worker, poll_interval=0.01)
    assert result["success"] is True

    # Verify employee status is back to IDLE
    refreshed_emp = await emp_repo.get_by_employee_id(emp.employee_id)
    assert refreshed_emp.status == EmployeeStatus.IDLE


@pytest.mark.asyncio
async def test_scheduler_supports_synchronous_worker_function(async_session: AsyncSession, tmp_path):
    """Verify TaskScheduler can run synchronous (non-async) worker functions without crashing."""
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)

    project = await proj_repo.create(name="Project-Sync-Worker", objective="Test sync worker")
    await task_repo.create(
        task_id="T-SYNC-01",
        project_id=project.id,
        title="Sync Task",
        objective="Run synchronous worker",
        scope="Core",
        acceptance_criteria="Complete",
        status=TaskStatus.PENDING
    )

    scheduler = TaskScheduler(task_repo=task_repo, verifier=IndependentVerifier())

    artifact = tmp_path / "sync-result.txt"
    def sync_worker(task):
        artifact.write_text("Synchronous worker deliverable", encoding="utf-8")
        return {"status": "success", "task_id": task.task_id, "mode": "sync", "artifacts": [str(artifact)]}

    result = await scheduler.execute_dag(project.id, worker_func=sync_worker, poll_interval=0.01)
    assert result["success"] is True
    assert "T-SYNC-01" in result["completed"]


@pytest.mark.asyncio
async def test_scheduler_cancellation_cleanup(async_session: AsyncSession):
    """Verify that cancelling execute_dag mid-flight cancels active worker tasks."""
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)

    project = await proj_repo.create(name="Project-Cancel", objective="Test cancellation")
    await task_repo.create(
        task_id="T-LONG-01",
        project_id=project.id,
        title="Long Running Task",
        objective="Run slow task",
        scope="Core",
        acceptance_criteria="Complete",
        status=TaskStatus.PENDING
    )

    scheduler = TaskScheduler(task_repo=task_repo)

    async def slow_worker(task):
        await asyncio.sleep(10.0)
        return {"status": "success"}

    dag_task = asyncio.create_task(
        scheduler.execute_dag(project.id, worker_func=slow_worker, poll_interval=0.01)
    )
    for _ in range(50):
        if len(scheduler._active_task_ids) == 1:
            break
        await asyncio.sleep(0.02)
    assert len(scheduler._active_task_ids) == 1

    dag_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dag_task

    assert len(scheduler._active_task_ids) == 0

