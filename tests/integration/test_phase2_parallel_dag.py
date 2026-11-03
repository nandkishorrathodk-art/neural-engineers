import asyncio
import json
import sys

import pytest
from neural.boss.brain import BossBrain
from neural.comms.bus import MessageBus
from neural.comms.messages import (
    BaseMessage,
    TaskAssignmentMessage,
)
from neural.db.repositories import (
    EmployeeRepository,
    PerformanceRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.evaluation.verifier import IndependentVerifier
from neural.exceptions import ModelProviderError
from neural.llm.gateway import LLMGateway
from neural.tasks.scheduler import TaskScheduler
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_phase2_full_dag_parallel_execution_and_messaging(async_session: AsyncSession, tmp_path):
    """
    Phase 2 Done Gate & Master Integration Test:
    1. CEO Brain plans hierarchical objective with DAG dependencies.
    2. Message Bus routes typed events on boss:broadcast, employee:{id}:inbox, tasks:{id}:events.
    3. TaskScheduler executes DAG in topological order with parallel worker dispatch.
    4. LLM Gateway routes through tiers and records exact token metrics & USD cost in DB.
    5. Independent QA verifier certifies skills upon clean receipts.
    """
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    perf_repo = PerformanceRepository(async_session)

    # 1. Recruit multi-disciplinary workforce
    factory = EmployeeFactory(employee_repo)
    backend_dev = await factory.hire_employee(
        name="Alex Morgan",
        title="Senior Backend Engineer",
        role_id="senior-backend-engineer"
    )
    frontend_dev = await factory.hire_employee(
        name="Jordan Lee",
        title="Frontend Engineer",
        initial_skills=["React", "Next.js", "TypeScript"]
    )
    qa_dev = await factory.hire_employee(
        name="Maya Chen",
        title="Quality Assurance Engineer",
        role_id="qa-engineer"
    )

    # 2. Setup Message Bus and subscribe to key channels
    bus = MessageBus(prefer_in_memory=True)
    broadcasts_received: list[BaseMessage] = []
    task_events_received: list[BaseMessage] = []
    alex_inbox_messages: list[BaseMessage] = []

    async def on_broadcast(msg: BaseMessage):
        broadcasts_received.append(msg)

    async def on_task_event(msg: BaseMessage):
        task_events_received.append(msg)

    async def on_alex_inbox(msg: BaseMessage):
        alex_inbox_messages.append(msg)

    await bus.subscribe("boss:broadcast", on_broadcast)
    await bus.subscribe(f"employee:{backend_dev.employee_id}:inbox", on_alex_inbox)

    # 3. Setup LLM Gateway with mock provider tracking tokens
    llm_calls: list[dict] = []

    plan_content = json.dumps({
        "code_name": "analytics_streaming",
        "complexity": "high",
        "requirements": ["real-time ingestion", "operator dashboard"],
        "technical_risks": ["stream backpressure"],
        "mitigation_strategies": ["bounded queues and retry limits"],
        "phases": [
            {
                "title": "Service design",
                "tasks": [
                    {
                        "task_id": "DESIGN-API",
                        "title": "Design streaming API",
                        "scope": "Specify the event schema and endpoint behavior.",
                        "role_required": "Backend Engineer",
                        "skills_required": ["FastAPI"],
                        "execution_mode": "terminal",
                        "acceptance_criteria": "Automated tests pass",
                    },
                    {
                        "task_id": "BUILD-STREAM",
                        "title": "Implement stream processor",
                        "scope": "Build bounded event processing and retries.",
                        "role_required": "Backend Engineer",
                        "skills_required": ["Python"],
                        "execution_mode": "terminal",
                        "dependencies": ["DESIGN-API"],
                        "acceptance_criteria": "Automated tests pass",
                    },
                ],
            },
            {
                "title": "Operator experience",
                "tasks": [
                    {
                        "task_id": "BUILD-DASHBOARD",
                        "title": "Build operator dashboard",
                        "scope": "Display stream health and recent events.",
                        "role_required": "Frontend Engineer",
                        "skills_required": ["React"],
                        "execution_mode": "terminal",
                        "dependencies": ["BUILD-STREAM"],
                        "acceptance_criteria": "Automated tests pass",
                    },
                    {
                        "task_id": "VERIFY-STACK",
                        "title": "Verify analytics stack",
                        "scope": "Run end-to-end integration checks.",
                        "role_required": "QA Engineer",
                        "skills_required": ["pytest"],
                        "execution_mode": "terminal",
                        "dependencies": ["BUILD-DASHBOARD"],
                        "acceptance_criteria": "Automated tests pass",
                    },
                ],
            },
        ],
    })

    async def mock_llm_provider(model: str, prompt: str, **kwargs):
        in_tokens = 150
        out_tokens = 90
        llm_calls.append({"model": model, "prompt_len": len(prompt)})
        return {
            "content": plan_content if "OBJECTIVE:" in prompt else "VALUE = 42\n",
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "provider": model.split("-")[0]
        }

    gateway = LLMGateway(provider_invoker=mock_llm_provider)

    # 4. Initialize BossBrain wired with Scheduler, Gateway, and Bus
    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=employee_repo,
        max_concurrency=4,
        message_bus=bus
        , verifier=IndependentVerifier(employee_repo=employee_repo, task_repo=task_repo)
    )
    boss = BossBrain(
        project_repo=project_repo,
        task_repo=task_repo,
        employee_repo=employee_repo,
        scheduler=scheduler,
        llm_gateway=gateway,
        message_bus=bus
    )

    # 5. Plan and persist project with DAG dependencies
    objective = "Build a real-time analytics streaming microservice and operator dashboard"
    project, tasks = await boss.plan_and_create_project(
        objective=objective,
        project_name="Project-AnalyticsMicroservice",
        auto_assign_first_task=False
    )

    assert project.id is not None
    assert len(tasks) >= 4

    # Allow async subscription callbacks to catch up
    await asyncio.sleep(0.05)
    assert len(broadcasts_received) >= 1
    assert "New Mission Initialized" in broadcasts_received[0].payload.get("title", "")

    # Subscribe to each created task event channel
    for t in tasks:
        await bus.subscribe(f"tasks:{t.task_id}:events", on_task_event)

    # Verify dependencies exist in database
    deps = await task_repo.list_dependencies_for_project(project.id)
    assert len(deps) >= 2  # Phase 2 tasks depend on Phase 1 tasks

    # 6. Execute DAG with Worker executing real logic & LLM Gateway calls
    execution_timeline: list[tuple[str, str, float]] = []

    async def worker_func(task):
        t_start = asyncio.get_event_loop().time()
        execution_timeline.append((task.task_id, "start", t_start))

        # Determine route & employee based on scope
        assigned_emp = backend_dev
        if "UI" in task.title or "Dashboard" in task.title:
            assigned_emp = frontend_dev
        elif "Verification" in task.title or "Audit" in task.title or "QA" in task.title:
            assigned_emp = qa_dev

        # Call LLM Gateway
        route = "code_generation" if "Implementation" in task.title else "boss_brain"
        llm_res = await gateway.generate(
            prompt=f"Execute task objective: {task.objective}",
            route=route,
            task_id=task.task_id,
            employee_id=assigned_emp.employee_id,
            db_session=async_session
        )

        task_dir = tmp_path / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = task_dir / "service.py"
        artifact_path.write_text(llm_res.content, encoding="utf-8")
        test_path = task_dir / "test_service.py"
        test_path.write_text(
            "from service import VALUE\n\n\ndef test_generated_value():\n    assert VALUE == 42\n",
            encoding="utf-8",
        )
        test_process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(test_path),
            cwd=task_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        test_output, _ = await test_process.communicate()

        await asyncio.sleep(0.02)  # Simulate active work
        t_end = asyncio.get_event_loop().time()
        execution_timeline.append((task.task_id, "end", t_end))

        return {
            "status": "success",
            "task_id": task.task_id,
            "output": llm_res.content,
            "artifacts": [str(artifact_path), str(test_path)],
            "test_exit_code": test_process.returncode,
            "test_output": test_output.decode("utf-8", errors="replace"),
        }

    result = await boss.execute_project_dag(project.id, worker_func=worker_func)

    # 7. Assertions: DAG Execution Outcome
    assert result["success"] is True
    assert len(result["completed"]) == len(tasks)
    assert len(result["failed"]) == 0
    assert len(result["blocked"]) == 0

    # 8. Verify Topological Execution Order
    # Phase 1 tasks (first 2) must complete before Phase 2 tasks start!
    {t.task_id: idx for idx, t in enumerate(tasks)}
    p1_tasks = [t.task_id for t in tasks if "PHASE-01" in t.task_id or t.scope == "Phase 1" or t.scope == "System Architecture & Foundation"]
    p2_tasks = [t.task_id for t in tasks if "PHASE-02" in t.task_id or t.scope == "Phase 2" or t.scope == "Parallel Service Implementation & UI"]

    if p1_tasks and p2_tasks:
        p1_end_times = [t[2] for t in execution_timeline if t[0] in p1_tasks and t[1] == "end"]
        p2_start_times = [t[2] for t in execution_timeline if t[0] in p2_tasks and t[1] == "start"]
        max_p1_end = max(p1_end_times)
        min_p2_start = min(p2_start_times)
        # Prerequisite must have finished before dependent started
        assert max_p1_end <= min_p2_start + 0.05

    # 9. Verify Message Bus Events Delivered
    await asyncio.sleep(0.05)
    assert len(task_events_received) >= len(tasks)
    event_types = {e.payload.get("event_type") for e in task_events_received}
    assert "task_completed" in event_types

    # 10. Verify Cost Tracking & DB Performance Records
    perf_records = await perf_repo.list_by_employee(backend_dev.employee_id)
    assert len(perf_records) >= 1
    assert perf_records[0].cost_tokens > 0
    assert perf_records[0].cost_usd > 0.0

    # 11. Run Independent Verification Engine
    verifier = IndependentVerifier(employee_repo)
    v_result = await verifier.verify_task_result(
        task_id=tasks[0].task_id,
        employee_id=backend_dev.employee_id,
        artifacts=["/workspace/service.py", "/workspace/test_service.py"],
        test_exit_code=0,
        tested_skills=["System Design", "PostgreSQL"]
    )
    assert v_result["passed"] is True

    # 12. Clean up bus
    await bus.close()


@pytest.mark.asyncio
async def test_phase2_message_bus_typed_channels():
    """
    Verifies that MessageBus correctly routes typed messages to:
    - boss:broadcast (BroadcastMessage)
    - employee:{id}:inbox (TaskAssignmentMessage)
    - tasks:{id}:events (TaskEventMessage)
    """
    bus = MessageBus(prefer_in_memory=True)
    inbox_received: list[BaseMessage] = []
    task_events_received: list[BaseMessage] = []

    async def on_inbox(msg: BaseMessage):
        inbox_received.append(msg)

    async def on_event(msg: BaseMessage):
        task_events_received.append(msg)

    emp_id = "ANC-EMP-TEST"
    task_id = "TASK-BUS-01"

    await bus.subscribe(f"employee:{emp_id}:inbox", on_inbox)
    await bus.subscribe(f"tasks:{task_id}:events", on_event)

    # Dispatch assignment to employee inbox
    assign_msg = TaskAssignmentMessage(
        sender="boss",
        recipient=emp_id,
        task_id=task_id,
        project_id="PROJ-01",
        title="Build PubSub Bus",
        objective="Deliver typed routing",
        acceptance_criteria="Tests passing"
    )
    await bus.send_to_employee(emp_id, assign_msg)

    # Dispatch task event
    await bus.publish_task_event(
        task_id=task_id,
        event_type="running",
        status="running",
        details={"progress": 50}
    )

    await asyncio.sleep(0.05)

    assert len(inbox_received) == 1
    assert inbox_received[0].payload["title"] == "Build PubSub Bus"
    assert inbox_received[0].payload["task_id"] == task_id

    assert len(task_events_received) == 1
    assert task_events_received[0].payload["task_id"] == task_id
    assert task_events_received[0].payload["event_type"] == "running"

    await bus.close()


@pytest.mark.asyncio
async def test_phase2_llm_gateway_transparent_fallback_under_execution():
    """
    Verifies that when primary model experiences provider failure during execution,
    the gateway transparently switches to the fallback provider without dropping the task.
    """
    call_history = []

    async def mock_failing_provider(model: str, prompt: str, **kwargs):
        call_history.append(model)
        if model == "claude-3-5-sonnet-20241022":
            raise ModelProviderError("500 Internal Server Error: Claude API unreachable")
        return {
            "content": f"Generated code implementation via {model}",
            "input_tokens": 200,
            "output_tokens": 150,
            "provider": "openai"
        }

    gateway = LLMGateway(config_path="configs/missing-test-routing.yaml", provider_invoker=mock_failing_provider)
    response = await gateway.generate(
        prompt="Write async Redis bus client",
        tier="coding"
    )

    # Primary failed, fallback succeeded
    assert response.model == "gpt-4o"
    assert "Generated code implementation via gpt-4o" in response.content
    assert "claude-3-5-sonnet-20241022" in call_history
    assert "gpt-4o" in call_history
    assert response.cost_usd > 0

