import pytest
from neural.constants import SkillLevel, TaskStatus
from neural.db.repositories import (
    EmployeeRepository,
    EvolutionRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.evolution.curriculum import CurriculumGenerator
from neural.evolution.gap_analysis import SkillGapAnalyzer
from neural.evolution.lifecycle import SelfEvolutionEngine
from neural.evolution.progression import SkillProgressionTracker
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_skill_gap_analysis_from_failed_task(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(name="Dave Developer", title="Backend Dev", initial_skills=["Python"])
    proj = await proj_repo.create(name="Project-Gap-Test", objective="Test gap analysis")

    # Failed task requiring Redis capability, which employee lacks verified proof for
    await task_repo.create(
        task_id="TASK-FAIL-GAP-01",
        project_id=proj.id,
        title="Redis Caching Layer",
        objective="Implement Redis cluster caching",
        scope="Backend",
        acceptance_criteria="Green tests",
        required_capabilities=["Redis"],
        assigned_to=emp.id,
        status=TaskStatus.FAILED,
    )

    analyzer = SkillGapAnalyzer(
        employee_repo=emp_repo,
        task_repo=task_repo,
        project_repo=proj_repo,
    )
    report = await analyzer.analyze_gaps()

    assert report.total_gaps >= 1
    assert report.critical_count >= 1
    redis_gap = next((g for g in report.gaps if g.skill_name == "Redis"), None)
    assert redis_gap is not None
    assert redis_gap.urgency == "critical"
    assert "TASK-FAIL-GAP-01" in redis_gap.source_task_ids
    assert len(report.recommendations) >= 1


@pytest.mark.asyncio
async def test_skill_gap_analysis_from_scheduled_project(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    await factory.hire_employee(name="Alice QA", title="QA Engineer", initial_skills=["pytest"])
    proj = await proj_repo.create(name="Project-K8s-Deploy", objective="Kubernetes Deployment")

    # Pending task requiring Kubernetes, which zero workforce employees possess
    await task_repo.create(
        task_id="TASK-PENDING-K8S",
        project_id=proj.id,
        title="K8s Helm Deployment",
        objective="Deploy to staging K8s cluster",
        scope="DevOps",
        acceptance_criteria="Manifests apply cleanly",
        required_capabilities=["Kubernetes"],
        status=TaskStatus.PENDING,
    )

    analyzer = SkillGapAnalyzer(
        employee_repo=emp_repo,
        task_repo=task_repo,
        project_repo=proj_repo,
    )
    report = await analyzer.analyze_gaps()

    k8s_gap = next((g for g in report.gaps if g.skill_name == "Kubernetes"), None)
    assert k8s_gap is not None
    assert k8s_gap.urgency == "high"
    assert "zero workforce employees" in k8s_gap.reason.lower()


def test_curriculum_generator():
    generator = CurriculumGenerator()

    # 1. Python curriculum
    py_curr = generator.generate_curriculum("Python", SkillLevel.COMPETENT, gap_reason="Failed webhook tasks")
    assert py_curr.skill_name == "Python"
    assert py_curr.target_level == "competent"
    assert len(py_curr.modules) >= 2
    assert "Failed webhook tasks" in py_curr.description
    assert any("aggregation" in m.title.lower() for m in py_curr.modules)

    # 2. FastAPI curriculum
    fastapi_curr = generator.generate_curriculum("FastAPI", SkillLevel.EXPERT)
    assert fastapi_curr.skill_name == "FastAPI"
    assert len(fastapi_curr.modules) >= 1
    assert "FastAPI" in fastapi_curr.modules[0].title


def test_skill_progression_tracker_ranks_and_tier_upgrade():
    assert SkillProgressionTracker.get_tier_rank(SkillLevel.NOVICE) == 1
    assert SkillProgressionTracker.get_tier_rank(SkillLevel.COMPETENT) == 2
    assert SkillProgressionTracker.get_tier_rank(SkillLevel.EXPERT) == 3
    assert SkillProgressionTracker.get_tier_rank(SkillLevel.MASTER) == 4

    # Progression steps
    assert SkillProgressionTracker.get_next_tier(SkillLevel.NOVICE) == SkillLevel.COMPETENT
    assert SkillProgressionTracker.get_next_tier(SkillLevel.COMPETENT) == SkillLevel.EXPERT
    assert SkillProgressionTracker.get_next_tier(SkillLevel.EXPERT) == SkillLevel.MASTER
    assert SkillProgressionTracker.get_next_tier(SkillLevel.MASTER) is None

    # Upgrade eligibility
    assert SkillProgressionTracker.can_upgrade(SkillLevel.NOVICE, SkillLevel.COMPETENT) is True
    assert SkillProgressionTracker.can_upgrade(SkillLevel.EXPERT, SkillLevel.COMPETENT) is False


@pytest.mark.asyncio
async def test_skill_progression_upgrade_and_matrix(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(
        name="Clara Oswald",
        title="Systems Architect",
        initial_skills=["Python", "System Design"],
    )

    tracker = SkillProgressionTracker(employee_repo=emp_repo)

    # Empirically upgrade Python from intermediate/novice to Expert
    res = await tracker.upgrade_employee_skill(
        employee_id=emp.employee_id,
        skill_name="Python",
        target_level=SkillLevel.EXPERT,
        evidence_id="TASK-EVIDENCE-400",
    )
    assert res["upgraded"] is True
    assert res["new_level"] == SkillLevel.EXPERT.value
    assert res["verified"] is True

    # Generate workforce matrix
    employees = await emp_repo.list_all()
    matrix_data = tracker.generate_matrix(employees)
    assert matrix_data["total_employees"] >= 1
    assert "Python" in matrix_data["skills_catalog"]

    clara_row = next(r for r in matrix_data["matrix"] if r["employee_id"] == emp.employee_id)
    assert clara_row["skills"]["Python"]["level"] == SkillLevel.EXPERT.value
    assert clara_row["skills"]["Python"]["verified"] is True


@pytest.mark.asyncio
async def test_self_evolution_engine_full_cycle(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    evo_repo = EvolutionRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    await factory.hire_employee(name="Sam Wilson", title="Engineer", initial_skills=["Python"])

    engine = SelfEvolutionEngine(
        evolution_repo=evo_repo,
        employee_repo=emp_repo,
        task_repo=task_repo,
        project_repo=proj_repo,
    )

    cycle_res = await engine.run_evolution_cycle(trigger_reason="weekly_governance_review")

    assert cycle_res["status"] == "completed"
    assert "cycle_id" in cycle_res
    assert len(cycle_res["hypotheses"]) >= 1
    assert len(cycle_res["curricula"]) >= 1
    assert len(cycle_res["persisted_record_ids"]) >= 1

    # Verify records persisted in database
    records = await evo_repo.list_all()
    assert len(records) >= 1
    assert records[0].record_type == "curriculum"
    assert records[0].status == "completed"

    # Verify real sandbox receipts are present in experiments
    assert len(cycle_res["experiments"]) >= 1
    first_exp = cycle_res["experiments"][0]
    assert "receipts" in first_exp
    assert first_exp["experiment_pass_rate"] > 0


def test_curriculum_generator_multi_tier_and_domain_coverage():
    generator = CurriculumGenerator()

    # Novice vs Master Python curricula must have distinct modules and titles
    py_nov = generator.generate_curriculum("Python", SkillLevel.NOVICE)
    py_mst = generator.generate_curriculum("Python", SkillLevel.MASTER)
    assert any("novice" in m.title.lower() for m in py_nov.modules)
    assert any("master" in m.title.lower() for m in py_mst.modules)
    assert py_nov.modules[0].module_id != py_mst.modules[0].module_id

    # Security curriculum
    sec_curr = generator.generate_curriculum("Security Auditing", SkillLevel.COMPETENT)
    assert any("security" in m.title.lower() for m in sec_curr.modules)
    assert "MOD-SEC" in sec_curr.modules[0].module_id

    # Docker curriculum
    docker_curr = generator.generate_curriculum("Docker", SkillLevel.COMPETENT)
    assert any("docker" in m.title.lower() for m in docker_curr.modules)

    # System Design curriculum
    sys_curr = generator.generate_curriculum("System Design", SkillLevel.COMPETENT)
    assert any("system design" in m.title.lower() for m in sys_curr.modules)
