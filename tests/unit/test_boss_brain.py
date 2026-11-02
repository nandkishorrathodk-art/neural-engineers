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

    async def invoke(**_kwargs):
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
                            "execution_mode": "gui",
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
                            "execution_mode": "hybrid",
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
    assert first["execution_mode"] == "gui"
    assert second["execution_mode"] == "hybrid"


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


