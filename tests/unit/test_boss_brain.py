import pytest
from neural.boss.brain import BossBrain


@pytest.mark.asyncio
async def test_boss_brain_status():
    brain = BossBrain()
    status = await brain.get_status()
    assert status["ceo_status"] == "operational"
    assert "active_mode" in status


@pytest.mark.asyncio
async def test_boss_brain_analyze_and_plan():
    brain = BossBrain()
    objective = "Build a complete production-grade e-commerce application"
    plan = await brain.analyze_and_plan(objective)
    assert "phases" in plan
    assert len(plan["phases"]) >= 3
    assert plan["phases"][0]["title"] == "System Architecture & Foundation"


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


