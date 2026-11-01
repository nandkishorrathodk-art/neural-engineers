import asyncio

import pytest
from neural.constants import EmployeeStatus, TaskPriority, TaskStatus
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository
from neural.exceptions import TaskDependencyError
from neural.tasks.dependencies import TaskGraph
from neural.tasks.scheduler import TaskScheduler
from sqlalchemy.ext.asyncio import AsyncSession


def test_task_graph_basic_dag_and_topological_sort():
    graph = TaskGraph()
    graph.add_task("task-1", title="Database Schema")
    graph.add_task("task-2", title="API Gateway")
    graph.add_task("task-3", title="Frontend UI")
    graph.add_task("task-4", title="End-to-End Testing")

    # API Gateway depends on Database Schema
    graph.add_dependency("task-2", "task-1")
    # Frontend depends on API Gateway
    graph.add_dependency("task-3", "task-2")
    # Testing depends on both API Gateway and Frontend
    graph.add_dependency("task-4", "task-2")
    graph.add_dependency("task-4", "task-3")

    order = graph.topological_sort()
    assert order.index("task-1") < order.index("task-2")
    assert order.index("task-2") < order.index("task-3")
    assert order.index("task-3") < order.index("task-4")


def test_task_graph_linear_cycle_detection():
    graph = TaskGraph()
    graph.add_task("A")
    graph.add_task("B")
    graph.add_dependency("B", "A")  # A -> B

    with pytest.raises(TaskDependencyError) as exc_info:
        graph.add_dependency("A", "B")  # B -> A creates cycle!
    assert "cycle" in str(exc_info.value).lower()


def test_task_graph_multi_node_cycle_detection():
    graph = TaskGraph()
    graph.add_task("A")
    graph.add_task("B")
    graph.add_task("C")

    graph.add_dependency("B", "A")  # A -> B
    graph.add_dependency("C", "B")  # B -> C

    with pytest.raises(TaskDependencyError) as exc_info:
        graph.add_dependency("A", "C")  # C -> A creates cycle A -> B -> C -> A
    assert "cycle" in str(exc_info.value).lower()


def test_task_graph_self_dependency_detection():
    graph = TaskGraph()
    graph.add_task("A")
    with pytest.raises(TaskDependencyError) as exc_info:
        graph.add_dependency("A", "A")
    assert "self-dependency" in str(exc_info.value).lower()


def test_task_graph_ready_tasks_parallel_branches():
    graph = TaskGraph()
    # A and B are independent root tasks
    # C depends on A
    # D depends on A and B
    graph.add_task("A")
    graph.add_task("B")
    graph.add_task("C")
    graph.add_task("D")

    graph.add_dependency("C", "A")
    graph.add_dependency("D", "A")
    graph.add_dependency("D", "B")

    # Initial state: only A and B have zero dependencies
    ready = set(graph.get_ready_tasks(completed_task_ids=set()))
    assert ready == {"A", "B"}

    # After A completes, C is ready, but D is NOT ready (still waiting on B)
    ready_after_a = set(graph.get_ready_tasks(completed_task_ids={"A"}))
    assert ready_after_a == {"B", "C"}

    # After both A and B complete, both C and D are ready
    ready_after_ab = set(graph.get_ready_tasks(completed_task_ids={"A", "B"}))
    assert ready_after_ab == {"C", "D"}


def test_task_graph_critical_path():
    graph = TaskGraph()
    graph.add_task("A")
    graph.add_task("B")
    graph.add_task("C")
    graph.add_task("D")

    # Path 1: A -> B -> D (length 3)
    # Path 2: A -> C -> D (length 3)
    graph.add_dependency("B", "A")
    graph.add_dependency("C", "A")
    graph.add_dependency("D", "B")
    graph.add_dependency("D", "C")

    path = graph.critical_path()
    assert len(path) == 3
    assert path[0] == "A"
    assert path[-1] == "D"


def test_task_graph_from_execution_plan():
    plan = {
        "phases": [
            {
                "phase_id": "P1",
                "tasks": [
                    {"task_id": "T1", "title": "Schema", "dependencies": []},
                    {"task_id": "T2", "title": "Gateway", "dependencies": []}
                ]
            },
            {
                "phase_id": "P2",
                "tasks": [
                    {"task_id": "T3", "title": "Services", "dependencies": ["P1"]},
                    {"task_id": "T4", "title": "UI", "dependencies": ["P1"]}
                ]
            },
            {
                "phase_id": "P3",
                "tasks": [
                    {"task_id": "T5", "title": "Verification", "dependencies": ["P2"]}
                ]
            }
        ]
    }
    graph = TaskGraph.from_execution_plan(plan)
    assert set(graph.get_all_tasks()) == {"T1", "T2", "T3", "T4", "T5"}
    assert set(graph.get_dependencies("T3")) == {"T1", "T2"}
    assert set(graph.get_dependencies("T4")) == {"T1", "T2"}
    assert set(graph.get_dependencies("T5")) == {"T3", "T4"}


@pytest.mark.asyncio
async def test_scheduler_dispatches_parallel_tasks_without_race_conditions(async_session: AsyncSession):
    """
    Verifies that the DAG scheduler dispatches multiple independent ready tasks
    simultaneously in parallel while respecting concurrency bounds and thread-safe locks.
    """
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)

    # 1. Create project
    project = await project_repo.create(name="Project-Parallel", objective="Verify parallel dispatch")

    # 2. Create 2 independent tasks (no prerequisites)
    t1 = await task_repo.create(
        task_id="TASK-PARALLEL-01",
        project_id=project.id,
        title="Parallel Branch Alpha",
        objective="Execute independently",
        scope="Branch A",
        acceptance_criteria="Receipt valid",
        status=TaskStatus.PENDING,
        priority=TaskPriority.HIGH
    )
    t2 = await task_repo.create(
        task_id="TASK-PARALLEL-02",
        project_id=project.id,
        title="Parallel Branch Beta",
        objective="Execute independently",
        scope="Branch B",
        acceptance_criteria="Receipt valid",
        status=TaskStatus.PENDING,
        priority=TaskPriority.HIGH
    )
    # Task 3 depends on both Task 1 and 2
    t3 = await task_repo.create(
        task_id="TASK-PARALLEL-03",
        project_id=project.id,
        title="Joined Downstream Task",
        objective="Wait for both branches",
        scope="Join",
        acceptance_criteria="Receipt valid",
        status=TaskStatus.PENDING,
        priority=TaskPriority.MEDIUM
    )
    await task_repo.add_dependency(t3.task_id, t1.task_id)
    await task_repo.add_dependency(t3.task_id, t2.task_id)

    # 3. Create 2 employees
    await employee_repo.create(
        employee_id="ANC-EMP-P1",
        name="Developer One",
        title="Backend Engineer",
        system_prompt="...",
        status=EmployeeStatus.IDLE
    )
    await employee_repo.create(
        employee_id="ANC-EMP-P2",
        name="Developer Two",
        title="Backend Engineer",
        system_prompt="...",
        status=EmployeeStatus.IDLE
    )

    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=employee_repo,
        max_concurrency=4
    )

    # 4. Concurrently trigger dispatch_ready_tasks from multiple coroutines to test race condition resilience
    results = await asyncio.gather(
        scheduler.dispatch_ready_tasks(project.id),
        scheduler.dispatch_ready_tasks(project.id)
    )

    all_dispatched = results[0] + results[1]
    # Exactly 2 tasks should have been dispatched (t1 and t2), not duplicated across coroutines!
    dispatched_ids = [t.task_id for t in all_dispatched]
    assert len(dispatched_ids) == 2
    assert set(dispatched_ids) == {"TASK-PARALLEL-01", "TASK-PARALLEL-02"}
    # Task 3 should NOT be dispatched because prerequisites have not completed
    assert "TASK-PARALLEL-03" not in dispatched_ids


@pytest.mark.asyncio
async def test_scheduler_enforces_concurrency_limits(async_session: AsyncSession):
    """
    Verifies that when concurrency limit is 1, only 1 task is dispatched even if multiple are ready.
    """
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)

    project = await project_repo.create(name="Project-Limit", objective="Verify limit")
    await task_repo.create(
        task_id="TASK-LIM-01",
        project_id=project.id,
        title="Limit Task 1",
        objective="Obj 1",
        scope="Scope 1",
        acceptance_criteria="A1",
        status=TaskStatus.PENDING
    )
    await task_repo.create(
        task_id="TASK-LIM-02",
        project_id=project.id,
        title="Limit Task 2",
        objective="Obj 2",
        scope="Scope 2",
        acceptance_criteria="A2",
        status=TaskStatus.PENDING
    )
    await employee_repo.create(
        employee_id="ANC-EMP-L1",
        name="Solo Worker",
        title="Engineer",
        system_prompt="...",
        status=EmployeeStatus.IDLE
    )

    # Scheduler with max_concurrency = 1
    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=employee_repo,
        max_concurrency=1
    )

    dispatched = await scheduler.dispatch_ready_tasks(project.id)
    assert len(dispatched) == 1
    assert dispatched[0].status == TaskStatus.RUNNING

    # Attempting to dispatch again while 1 is running should dispatch 0
    second_dispatch = await scheduler.dispatch_ready_tasks(project.id)
    assert len(second_dispatch) == 0


@pytest.mark.asyncio
async def test_scheduler_failure_cascades_blocks_and_prevents_deadlock(async_session: AsyncSession):
    """
    Verifies that if an upstream prerequisite fails, all dependent tasks are automatically
    transitioned to BLOCKED, preventing deadlock.
    """
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)

    project = await project_repo.create(name="Project-Failure", objective="Verify failure cascading")
    t1 = await task_repo.create(
        task_id="TASK-FAIL-ROOT",
        project_id=project.id,
        title="Root Failing Task",
        objective="Fails intentionally",
        scope="Root",
        acceptance_criteria="Clean failure test",
        status=TaskStatus.PENDING
    )
    t2 = await task_repo.create(
        task_id="TASK-DEPENDENT-CHILD",
        project_id=project.id,
        title="Child Dependent Task",
        objective="Should become blocked",
        scope="Child",
        acceptance_criteria="Must not run",
        status=TaskStatus.PENDING
    )
    await task_repo.add_dependency(t2.task_id, t1.task_id)

    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=employee_repo,
        max_concurrency=2
    )

    # Custom worker that intentionally fails t1
    async def failing_worker(task):
        await asyncio.sleep(0.01)
        return {"status": "failure", "error": "Simulated compilation crash"}

    result = await scheduler.execute_dag(project.id, worker_func=failing_worker)

    assert result["success"] is False
    assert "TASK-FAIL-ROOT" in result["failed"]
    assert "TASK-DEPENDENT-CHILD" in result["blocked"]

    # Verify status in database
    reloaded_child = await task_repo.get_by_task_id(t2.task_id)
    assert reloaded_child.status == TaskStatus.BLOCKED


def test_task_graph_edge_cases():
    graph = TaskGraph()
    # 1. Empty graph
    assert graph.topological_sort() == []
    assert graph.critical_path() == []
    assert graph.get_ready_tasks(set()) == []
    assert graph.get_dependencies("NON-EXISTENT") == []
    assert graph.get_dependents("NON-EXISTENT") == []

    # 2. Disconnected components
    graph.add_task("G1_A")
    graph.add_task("G1_B")
    graph.add_dependency("G1_B", "G1_A")

    graph.add_task("G2_X")
    graph.add_task("G2_Y")
    graph.add_dependency("G2_Y", "G2_X")

    order = graph.topological_sort()
    assert len(order) == 4
    assert order.index("G1_A") < order.index("G1_B")
    assert order.index("G2_X") < order.index("G2_Y")

    ready = set(graph.get_ready_tasks(set()))
    assert ready == {"G1_A", "G2_X"}


@pytest.mark.asyncio
async def test_scheduler_empty_project_execution(async_session: AsyncSession):
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)

    project = await project_repo.create(name="Project-Empty", objective="No tasks")
    scheduler = TaskScheduler(task_repo=task_repo, employee_repo=employee_repo)

    result = await scheduler.execute_dag(project.id)
    assert result["total_tasks"] == 0
    assert result["completed"] == []
    assert result["success"] is True

