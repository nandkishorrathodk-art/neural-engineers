import pytest
from neural.boss.brain import BossBrain


@pytest.mark.asyncio
async def test_boss_brain_status():
    brain = BossBrain()
    status = await brain.get_status()
    assert status["ceo_status"] in {"ready", "rules_only"}
    assert status["active_mode"] in {"planning_and_delegation", "rules_based_planning"}
    assert status["worker_runtime"] == "not_reported"
    assert status["system_health"] == "degraded"


@pytest.mark.asyncio
async def test_boss_brain_status_uses_authenticated_freshness_receipts(
    async_session,
    monkeypatch,
):
    import json
    from datetime import datetime, timedelta, timezone

    from neural.config import get_settings
    from neural.db.repositories import ServiceHeartbeatRepository

    monkeypatch.setenv("WORKER_API_TOKEN", "w" * 40)
    monkeypatch.setenv(
        "WORKER_API_TOKENS",
        json.dumps({"worker-service": "w" * 40, "worker-secondary": "x" * 40}),
    )
    monkeypatch.setenv("VERIFIER_API_TOKEN", "v" * 40)
    get_settings.cache_clear()
    runtime_repo = ServiceHeartbeatRepository(async_session)
    await runtime_repo.record("worker", "worker-service", "a" * 32)
    await runtime_repo.record("verifier", "verification-service", "b" * 32)

    brain = BossBrain(service_heartbeat_repo=runtime_repo)
    partial = await brain.get_status()
    assert partial["worker_runtime"] == "degraded"

    await runtime_repo.record("worker", "worker-secondary", "c" * 32)
    status = await brain.get_status()
    assert status["worker_runtime"] == "healthy"
    assert status["verifier_runtime"] == "healthy"
    assert status["system_health"] == "healthy"
    assert len(status["service_runtimes"]) == 3

    rows = await runtime_repo.list_all()
    rows[0].last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2)
    await async_session.commit()
    stale = await brain.get_status()
    assert "stale" in {item["status"] for item in stale["service_runtimes"]}
    assert stale["system_health"] == "degraded"


@pytest.mark.asyncio
async def test_boss_brain_status_does_not_count_short_or_shared_service_tokens(monkeypatch):
    from neural.config import get_settings

    owner_token = "o" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", "short-worker-token")
    monkeypatch.setenv("VERIFIER_API_TOKEN", owner_token)
    get_settings.cache_clear()

    status = await BossBrain().get_status()
    assert status["worker_auth_configured"] is False
    assert status["verifier_auth_configured"] is False
    assert status["worker_runtime"] == "not_reported"
    assert status["system_health"] == "degraded"


@pytest.mark.asyncio
async def test_boss_brain_analyze_and_plan():
    brain = BossBrain()
    objective = "Build a complete production-grade e-commerce application"
    plan = await brain.analyze_and_plan(objective)
    assert "phases" in plan
    assert len(plan["phases"]) >= 3
    assert plan["phases"][0]["title"] == "System Architecture & Foundation"


@pytest.mark.asyncio
async def test_boss_planner_uses_configured_llm_and_rewrites_task_dependencies(monkeypatch):
    import json

    from neural.boss.planner import TaskPlanner
    from neural.llm.gateway import LLMGateway

    captured_prompts = []

    async def invoke(**kwargs):
        captured_prompts.append(kwargs["prompt"])
        return {
            "content": json.dumps({
                "code_name": "Sample",
                "complexity": "low",
                "requirements": ["Create a concise web page"],
                "technical_risks": ["Requirements may be incomplete"],
                "mitigation_strategies": ["Ask for clarification on blockers"],
                "phases": [{
                    "title": "Build and verify",
                    "tasks": [
                        {
                            "task_id": "draft",
                            "title": "Create the page",
                            "objective": "Create the requested company page.",
                            "scope": "Frontend implementation",
                            "role_required": "frontend-engineer",
                            "skills_required": ["HTML"],
                            "execution_mode": "terminal",
                            "dependencies": [],
                            "acceptance_criteria": "The page renders and contains the requested content.",
                            "subtasks": ["Draft the page"],
                            "estimated_hours": 1,
                            "max_tokens": 4000,
                        },
                        {
                            "task_id": "verify",
                            "title": "Verify the page",
                            "objective": "Check the created page against the requirements.",
                            "scope": "Independent review",
                            "role_required": "qa-engineer",
                            "skills_required": ["Browser QA"],
                            "execution_mode": "terminal",
                            "dependencies": ["draft"],
                            "acceptance_criteria": "A reviewer confirms the rendered page meets the criteria.",
                            "subtasks": ["Inspect the page"],
                            "estimated_hours": 1,
                            "max_tokens": 4000,
                        },
                    ],
                }],
            }),
            "input_tokens": 20,
            "output_tokens": 100,
            "provider": "test",
        }

    gateway = LLMGateway(provider_invoker=invoke)
    monkeypatch.setattr(gateway.settings, "BOSS_LLM_PLANNING_ENABLED", True)
    plan = await TaskPlanner(llm_gateway=gateway).decompose_objective("Create a small company page")

    first, second = plan["phases"][0]["tasks"]
    assert plan["plan_source"] == "llm"
    assert plan["planning_model"] == gateway.get_models_for_tier("reasoning")[0]
    assert second["dependencies"] == [first["task_id"]]
    assert first["execution_mode"] == "terminal"
    assert second["execution_mode"] == "terminal"
    prompt = captured_prompts[0]
    assert "Respect explicit atomicity constraints" in prompt
    assert "exactly one phase containing exactly one task" in prompt
    assert "For broader objectives without an explicit atomicity constraint" in prompt


@pytest.mark.asyncio
async def test_boss_planner_rejects_modes_without_worker_consumers(monkeypatch):
    import json

    from neural.boss.planner import TaskPlanner
    from neural.exceptions import UnsupportedExecutionModeError
    from neural.llm.gateway import LLMGateway

    async def invoke(**_kwargs):
        return {
            "content": json.dumps({
                "code_name": "GuiOnly",
                "complexity": "low",
                "requirements": ["Use the desktop"],
                "technical_risks": [],
                "mitigation_strategies": [],
                "phases": [{
                    "title": "Desktop work",
                    "tasks": [{
                        "task_id": "gui-task",
                        "title": "Operate the desktop",
                        "objective": "Complete the requested visible desktop workflow.",
                        "scope": "GUI workflow",
                        "role_required": "qa-engineer",
                        "skills_required": ["Visual QA"],
                        "execution_mode": "gui",
                        "dependencies": [],
                        "acceptance_criteria": "A visual verifier confirms the final desktop state.",
                        "subtasks": [],
                        "estimated_hours": 1,
                        "max_tokens": 4000,
                    }],
                }],
            }),
            "input_tokens": 20,
            "output_tokens": 100,
            "provider": "test",
        }

    gateway = LLMGateway(provider_invoker=invoke)
    monkeypatch.setattr(gateway.settings, "BOSS_LLM_PLANNING_ENABLED", True)
    # A deployment that only runs the terminal worker must still refuse GUI work;
    # gui/hybrid are accepted only when their workers are configured.
    monkeypatch.setattr(gateway.settings, "BOSS_SUPPORTED_EXECUTION_MODES", ["terminal"])
    with pytest.raises(UnsupportedExecutionModeError, match="gui"):
        await TaskPlanner(llm_gateway=gateway).decompose_objective("Use the desktop to verify this workflow")


@pytest.mark.asyncio
async def test_boss_planner_accepts_gui_when_gui_worker_is_configured(monkeypatch):
    import json

    from neural.boss.planner import TaskPlanner
    from neural.constants import ExecutionMode
    from neural.llm.gateway import LLMGateway

    async def invoke(**_kwargs):
        return {
            "content": json.dumps({
                "code_name": "GuiOnly",
                "complexity": "low",
                "requirements": ["Use the desktop"],
                "technical_risks": [],
                "mitigation_strategies": [],
                "phases": [{
                    "title": "Desktop work",
                    "tasks": [{
                        "task_id": "gui-task",
                        "title": "Operate the desktop",
                        "objective": "Complete the requested visible desktop workflow.",
                        "scope": "GUI workflow",
                        "role_required": "qa-engineer",
                        "skills_required": ["Visual QA"],
                        "execution_mode": "gui",
                        "dependencies": [],
                        "acceptance_criteria": "A visual verifier confirms the final desktop state.",
                        "subtasks": [],
                        "estimated_hours": 1,
                        "max_tokens": 4000,
                    }],
                }],
            }),
            "input_tokens": 20,
            "output_tokens": 100,
            "provider": "test",
        }

    gateway = LLMGateway(provider_invoker=invoke)
    monkeypatch.setattr(gateway.settings, "BOSS_LLM_PLANNING_ENABLED", True)
    monkeypatch.setattr(gateway.settings, "BOSS_SUPPORTED_EXECUTION_MODES", ["terminal", "gui", "hybrid"])
    plan = await TaskPlanner(llm_gateway=gateway).decompose_objective("Use the desktop to verify this workflow")
    assert plan["phases"][0]["tasks"][0]["execution_mode"] == ExecutionMode.GUI


def test_objective_schedule_requires_future_timezone_qualified_datetime():
    from datetime import datetime, timedelta, timezone

    from neural.api.schemas.requests import ObjectiveSubmissionRequest
    from pydantic import ValidationError

    valid_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    request = ObjectiveSubmissionRequest(objective="Build a report", scheduled_at=valid_time)
    assert request.scheduled_at is not None
    assert request.scheduled_at.tzinfo is None  # normalized UTC-naive value used by SQL storage

    with pytest.raises(ValidationError, match="timezone offset"):
        ObjectiveSubmissionRequest(objective="Build a report", scheduled_at="2030-01-01T00:00:00")


@pytest.mark.asyncio
async def test_boss_planner_preserves_model_clarification_without_creating_tasks(monkeypatch):
    import json

    from neural.boss.planner import TaskPlanner
    from neural.llm.gateway import LLMGateway

    async def invoke(**_kwargs):
        return {
            "content": json.dumps({
                "code_name": "Clarification",
                "complexity": "low",
                "requirements": [],
                "clarification_required": True,
                "clarifying_questions": ["Which system should be fixed, and what failure do you observe?"],
                "technical_risks": [],
                "mitigation_strategies": [],
                "phases": [],
            }),
            "input_tokens": 20,
            "output_tokens": 30,
            "provider": "test",
        }

    gateway = LLMGateway(provider_invoker=invoke)
    monkeypatch.setattr(gateway.settings, "BOSS_LLM_PLANNING_ENABLED", True)
    plan = await TaskPlanner(llm_gateway=gateway).decompose_objective("Please fix the service")

    assert plan["clarification_required"] is True
    assert plan["phases"] == []
    assert plan["clarifying_questions"] == ["Which system should be fixed, and what failure do you observe?"]


@pytest.mark.asyncio
async def test_boss_planner_uses_owner_objective_when_model_omits_requirements(monkeypatch):
    import json

    from neural.boss.planner import TaskPlanner
    from neural.llm.gateway import LLMGateway

    async def invoke(**_kwargs):
        return {
            "content": json.dumps({
                "code_name": "AtomicTest",
                "complexity": "low",
                "requirements": [],
                "technical_risks": [],
                "mitigation_strategies": [],
                "phases": [{
                    "title": "Implement and verify",
                    "tasks": [{
                        "task_id": "atomic",
                        "title": "Implement and test clamp",
                        "objective": "Write the implementation and tests in one file.",
                        "scope": "One self-contained Python file",
                        "role_required": "software-engineer",
                        "skills_required": ["Python", "unittest"],
                        "execution_mode": "terminal",
                        "dependencies": [],
                        "acceptance_criteria": "tests pass",
                        "subtasks": [],
                        "estimated_hours": 1,
                        "max_tokens": 4000,
                    }],
                }],
            }),
            "input_tokens": 20,
            "output_tokens": 100,
            "provider": "test",
        }

    gateway = LLMGateway(provider_invoker=invoke)
    monkeypatch.setattr(gateway.settings, "BOSS_LLM_PLANNING_ENABLED", True)
    objective = "Implement clamp and add tests in one atomic task."
    plan = await TaskPlanner(llm_gateway=gateway).decompose_objective(objective)

    assert plan["requirements"] == [f"Deliver the owner's stated objective: {objective}"]
    assert len(plan["phases"]) == 1
    assert len(plan["phases"][0]["tasks"]) == 1


@pytest.mark.asyncio
async def test_boss_brain_plan_and_create_project(async_session):
    from neural.db.repositories import ProjectRepository, TaskRepository
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    brain = BossBrain(project_repo=project_repo, task_repo=task_repo)

    objective = "Build a resilient message processing pipeline"
    project, tasks = await brain.plan_and_create_project(
        objective=objective,
        project_name="Project-Pipeline"
    )

    assert project.id is not None
    assert project.name == "Project-Pipeline"
    assert len(tasks) >= 4
    persisted = await task_repo.list_by_project(project.id)
    assert len(persisted) == len(tasks)
    assert {task.execution_mode for task in persisted} == {"terminal", "hybrid"}


@pytest.mark.asyncio
async def test_clarification_required_persists_zero_projects_or_tasks(async_session):
    from neural.db.repositories import ProjectRepository, TaskRepository
    from neural.exceptions import ClarificationRequiredError

    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    brain = BossBrain(project_repo=project_repo, task_repo=task_repo)

    # Vague objective triggers clarification
    vague_obj = "do it"
    with pytest.raises(ClarificationRequiredError) as exc_info:
        await brain.plan_and_create_project(objective=vague_obj)

    assert len(exc_info.value.questions) > 0

    # Verify zero projects and zero tasks were persisted (fail-closed)
    projects = await project_repo.list_all()
    assert len(projects) == 0
    all_tasks = await task_repo.list_all()
    assert len(all_tasks) == 0


@pytest.mark.asyncio
async def test_clarifications_answered_allows_planning_and_persistence(async_session):
    from neural.db.repositories import ProjectRepository, TaskRepository

    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    brain = BossBrain(project_repo=project_repo, task_repo=task_repo)

    vague_obj = "do it"
    clarifications = {
        "What specific outcome should ANCOS deliver, and what would count as success?": (
            "Build an autonomous math calculation utility package with comprehensive test coverage."
        )
    }

    project, tasks = await brain.plan_and_create_project(
        objective=vague_obj,
        clarifications=clarifications,
        project_name="Math-Utility-Project",
    )

    assert project.id is not None
    assert project.name == "Math-Utility-Project"
    assert len(tasks) >= 4
    persisted_projects = await project_repo.list_all()
    assert len(persisted_projects) == 1
    persisted_tasks = await task_repo.list_by_project(project.id)
    assert len(persisted_tasks) == len(tasks)


@pytest.mark.asyncio
async def test_dag_cycle_detection_via_kahns_algorithm():
    from neural.boss.planner import ObjectivePlan, PlannedPhase, PlannedTask, validate_plan_dag
    from neural.constants import ExecutionMode
    from neural.exceptions import ModelProviderError

    # Cycle: Task A depends on Task B, and Task B depends on Task A
    cyclic_plan = ObjectivePlan(
        code_name="CycleTest",
        complexity="low",
        phases=[
            PlannedPhase(
                title="Phase 1",
                tasks=[
                    PlannedTask(
                        task_id="TASK-A",
                        title="Task A",
                        scope="Scope A",
                        role_required="engineer",
                        skills_required=["Python"],
                        execution_mode=ExecutionMode.TERMINAL,
                        dependencies=["TASK-B"],
                        acceptance_criteria="passes test",
                    ),
                    PlannedTask(
                        task_id="TASK-B",
                        title="Task B",
                        scope="Scope B",
                        role_required="engineer",
                        skills_required=["Python"],
                        execution_mode=ExecutionMode.TERMINAL,
                        dependencies=["TASK-A"],
                        acceptance_criteria="passes test",
                    ),
                ],
            )
        ],
    )

    with pytest.raises(ModelProviderError, match="dependency cycle detected"):
        validate_plan_dag(cyclic_plan)


@pytest.mark.asyncio
async def test_dag_self_dependency_rejection():
    from neural.boss.planner import ObjectivePlan, PlannedPhase, PlannedTask, validate_plan_dag
    from neural.constants import ExecutionMode
    from neural.exceptions import ModelProviderError

    self_dep_plan = ObjectivePlan(
        code_name="SelfDepTest",
        complexity="low",
        phases=[
            PlannedPhase(
                title="Phase 1",
                tasks=[
                    PlannedTask(
                        task_id="TASK-SELF",
                        title="Task Self",
                        scope="Scope Self",
                        role_required="engineer",
                        skills_required=["Python"],
                        execution_mode=ExecutionMode.TERMINAL,
                        dependencies=["TASK-SELF"],
                        acceptance_criteria="passes test",
                    ),
                ],
            )
        ],
    )

    with pytest.raises(ModelProviderError, match="self-dependency in TASK-SELF"):
        validate_plan_dag(self_dep_plan)


@pytest.mark.asyncio
async def test_dag_duplicate_task_id_rejection():
    from neural.boss.planner import ObjectivePlan, PlannedPhase, PlannedTask, validate_plan_dag
    from neural.constants import ExecutionMode
    from neural.exceptions import ModelProviderError

    dup_id_plan = ObjectivePlan(
        code_name="DupIdTest",
        complexity="low",
        phases=[
            PlannedPhase(
                title="Phase 1",
                tasks=[
                    PlannedTask(
                        task_id="TASK-DUP",
                        title="Task 1",
                        scope="Scope 1",
                        role_required="engineer",
                        skills_required=["Python"],
                        execution_mode=ExecutionMode.TERMINAL,
                        dependencies=[],
                        acceptance_criteria="passes test",
                    ),
                    PlannedTask(
                        task_id="TASK-DUP",
                        title="Task 2",
                        scope="Scope 2",
                        role_required="engineer",
                        skills_required=["Python"],
                        execution_mode=ExecutionMode.TERMINAL,
                        dependencies=[],
                        acceptance_criteria="passes test",
                    ),
                ],
            )
        ],
    )

    with pytest.raises(ModelProviderError, match="duplicate task IDs"):
        validate_plan_dag(dup_id_plan)


@pytest.mark.asyncio
async def test_dag_forward_dependency_rejection():
    from neural.boss.planner import ObjectivePlan, PlannedPhase, PlannedTask, validate_plan_dag
    from neural.constants import ExecutionMode
    from neural.exceptions import ModelProviderError

    # TASK-1 depends on TASK-2 which is defined after TASK-1
    forward_plan = ObjectivePlan(
        code_name="ForwardTest",
        complexity="low",
        phases=[
            PlannedPhase(
                title="Phase 1",
                tasks=[
                    PlannedTask(
                        task_id="TASK-1",
                        title="Task 1",
                        scope="Scope 1",
                        role_required="engineer",
                        skills_required=["Python"],
                        execution_mode=ExecutionMode.TERMINAL,
                        dependencies=["TASK-2"],
                        acceptance_criteria="passes test",
                    ),
                    PlannedTask(
                        task_id="TASK-2",
                        title="Task 2",
                        scope="Scope 2",
                        role_required="engineer",
                        skills_required=["Python"],
                        execution_mode=ExecutionMode.TERMINAL,
                        dependencies=[],
                        acceptance_criteria="passes test",
                    ),
                ],
            )
        ],
    )

    with pytest.raises(ModelProviderError, match="invalid or forward dependency"):
        validate_plan_dag(forward_plan)


