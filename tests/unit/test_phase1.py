import os
import shutil

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.boss.planner import TaskPlanner
from neural.constants import ContainerStatus, SkillLevel
from neural.containers.manager import ContainerManager
from neural.db.repositories import (
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from sqlalchemy.ext.asyncio import AsyncSession

# =====================================================================
# 1. CEO Brain & Hierarchical Plan Decomposition Unit Tests
# =====================================================================

@pytest.mark.asyncio
async def test_planner_objective_analysis_and_complexity():
    planner = TaskPlanner()

    # Empty & whitespace-only objectives do not crash and produce safe baseline
    plan_empty = await planner.decompose_objective("")
    assert plan_empty["code_name"] == "Omega"
    assert plan_empty["complexity"] == "low"
    assert len(plan_empty["phases"]) >= 3

    plan_whitespace = await planner.decompose_objective("   \t\n  ")
    assert plan_whitespace["code_name"] == "Omega"

    # High complexity objective
    plan_high = await planner.decompose_objective(
        "Build a distributed enterprise microservices streaming pipeline with production security audit"
    )
    assert plan_high["complexity"] == "high"
    assert len(plan_high["requirements"]) >= 3
    assert "technical_risks" in plan_high["risk_analysis"]
    assert "mitigation_strategies" in plan_high["risk_analysis"]
    assert len(plan_high["risk_analysis"]["technical_risks"]) >= 2

    # Medium complexity objective
    complexity_mid = planner.score_complexity("Build an API backend with CRUD and SQLite database")
    assert complexity_mid in ("medium", "high")


@pytest.mark.asyncio
async def test_rules_planner_requests_clarification_for_under_specified_objectives():
    plan = await TaskPlanner().decompose_objective("fix it")

    assert plan["clarification_required"] is True
    assert plan["clarifying_questions"]
    assert plan["phases"] == []


@pytest.mark.asyncio
async def test_planner_subtasks_and_hierarchy():
    planner = TaskPlanner()
    plan = await planner.decompose_objective("Implement resilient user service")

    # Verify each phase has tasks, and tasks have explicit subtasks
    for phase in plan["phases"]:
        assert len(phase["tasks"]) >= 1
        for task in phase["tasks"]:
            assert "subtasks" in task
            assert len(task["subtasks"]) >= 1
            assert "resource_budget" in task


@pytest.mark.asyncio
async def test_task_repository_subtask_hierarchy(async_session: AsyncSession):
    p_repo = ProjectRepository(async_session)
    t_repo = TaskRepository(async_session)

    project = await p_repo.create(name="Project-Hierarchy", objective="Test subtasks")
    parent_task = await t_repo.create(
        task_id="TASK-PARENT-01",
        project_id=project.id,
        title="Parent Engineering Task",
        objective="Coordinate subtasks",
        scope="Architecture",
        acceptance_criteria="All subtasks passed"
    )

    subtask1 = await t_repo.create_subtask(
        parent_task_id=parent_task.task_id,
        title="Subtask 1: Define Interface"
    )
    subtask2 = await t_repo.create_subtask(
        parent_task_id=parent_task.task_id,
        title="Subtask 2: Unit Test Suite"
    )

    assert subtask1.parent_task_id == parent_task.id
    assert subtask2.parent_task_id == parent_task.id

    subtasks = await t_repo.list_subtasks(parent_task.task_id)
    assert len(subtasks) == 2
    titles = [s.title for s in subtasks]
    assert "Subtask 1: Define Interface" in titles
    assert "Subtask 2: Unit Test Suite" in titles


# =====================================================================
# 2. Isolated Docker Desktop Workstation Infrastructure Unit Tests
# =====================================================================

@pytest.mark.asyncio
async def test_container_manager_port_allocation_and_reuse():
    manager = ContainerManager()

    # Initial allocations start at 5901 (VNC) and 6081 (noVNC)
    v1, nv1 = manager.allocate_ports("emp-01")
    assert v1 == 5901
    assert nv1 == 6081

    v2, nv2 = manager.allocate_ports("emp-02")
    assert v2 == 5902
    assert nv2 == 6082

    # Querying existing identifier returns idempotent ports
    assert manager.allocate_ports("emp-01") == (5901, 6081)

    # Release emp-01 and verify reuse
    released = manager.release_ports("emp-01")
    assert released == (5901, 6081)

    v3, nv3 = manager.allocate_ports("emp-03")
    assert v3 == 5901
    assert nv3 == 6081

    # Cleanup
    manager.release_ports("emp-02")
    manager.release_ports("emp-03")


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("ANCOS_TEST_DOCKER") != "1",
    reason="Requires a dedicated disposable Docker daemon; never provision test desktops on the user's live host.",
)
async def test_container_manager_workstation_lifecycle_and_health():
    manager = ContainerManager()
    emp_id = "ANC-EMP-PHASE1-TEST"

    # Provision desktop workstation
    spec = await manager.provision_employee_desktop(
        employee_id=emp_id,
        role_image="employee-swe",
        cpu_limit=2.0,
        mem_limit="4g"
    )

    docker_id = spec["docker_id"]
    assert spec["vnc_port"] >= 5901
    assert spec["novnc_port"] >= 6081
    assert spec["user"] == "1000:1000"
    assert spec["privileged"] is False
    assert spec["pids_limit"] == 200
    assert os.path.exists(spec["workspace"])

    # Health check
    health = await manager.inspect_workstation_health(docker_id)
    assert health["healthy"] is True
    assert health["docker_id"] == docker_id
    assert health["vnc_port"] == spec["vnc_port"]
    assert health["user"] == "1000:1000"

    # Lookup by employee ID
    found_spec = await manager.get_desktop_by_employee_id(emp_id)
    assert found_spec is not None
    assert found_spec["docker_id"] == docker_id

    # Restart desktop (NFR-2)
    restarted = await manager.restart_desktop(docker_id)
    assert restarted["status"] == ContainerStatus.RUNNING.value
    assert restarted["docker_id"] == docker_id

    # Command execution
    exec_res = await manager.execute_command(docker_id, "whoami")
    assert exec_res["exit_code"] == 0

    # Build image check (validates Dockerfile.employee-base exists)
    built = await manager.build_employee_image()
    assert built is True

    # Terminate
    await manager.terminate_desktop(docker_id)
    status_after = await manager.get_desktop_status(docker_id)
    assert status_after["status"] == ContainerStatus.TERMINATED.value

    # Cleanup workspace
    if os.path.exists(spec["workspace"]):
        shutil.rmtree(spec["workspace"], ignore_errors=True)


# =====================================================================
# 3. Dynamic Initial Workforce Recruitment Unit Tests
# =====================================================================

@pytest.mark.asyncio
async def test_employee_factory_available_roles_and_dynamic_recruitment(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    # 1. Available roles from config
    roles = factory.list_available_roles()
    assert len(roles) >= 3
    role_ids = [r["id"] for r in roles]
    assert "senior-backend-engineer" in role_ids
    assert "qa-engineer" in role_ids

    # 2. Recruit by specific role
    devops = await factory.recruit_for_role("devops-engineer", name="Sam DevOps")
    assert devops.title == "DevOps & Infrastructure Engineer"
    assert devops.department == "Infrastructure"
    assert "terminal" in devops.approved_tools

    # 3. Recruit for specific skill
    react_dev = await factory.recruit_for_skill("React", name="Chris UI")
    assert "Frontend" in react_dev.title or "React" in react_dev.title
    assert "browser" in react_dev.approved_tools


@pytest.mark.asyncio
async def test_employee_factory_idempotent_bootstrap(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    # First bootstrap creates the 2 initial employees
    workforce1 = await factory.bootstrap_initial_workforce()
    assert len(workforce1) == 2
    first_backend = workforce1[0]
    first_qa = workforce1[1]
    assert len({employee.employee_id for employee in workforce1}) == 2
    assert len({employee.name for employee in workforce1}) == 2
    assert all(employee.name not in {"Alex Morgan", "Maya Chen"} for employee in workforce1)

    # Second bootstrap returns the existing employees without duplicating rows
    workforce2 = await factory.bootstrap_initial_workforce()
    assert len(workforce2) == 2
    assert workforce2[0].id == first_backend.id
    assert workforce2[1].id == first_qa.id

    all_employees = await repo.list_all()
    assert len(all_employees) == 2


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("ANCOS_TEST_DOCKER") != "1",
    reason="Requires a dedicated disposable Docker daemon; never provision test desktops on the user's live host.",
)
async def test_container_manager_cross_restart_persistence(async_session: AsyncSession):
    """
    Verifies that ContainerManager can recover workstation specs and prevent port collisions
    even after a complete server restart (fresh in-memory state).
    """
    container_repo = ContainerRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(employee_repo)

    emp = await factory.hire_employee(name="Jordan Lee", title="Systems Engineer")

    # Manager 1 provisions workstation
    mgr1 = ContainerManager()
    spec1 = await mgr1.provision_employee_desktop(
        employee_id=emp.employee_id,
        role_image="employee-swe",
        container_repo=container_repo,
        employee_repo=employee_repo
    )
    assert spec1["vnc_port"] == 5901
    assert spec1["novnc_port"] == 6081

    # Simulate server restart by creating Manager 2 with empty in-memory dictionaries
    mgr2 = ContainerManager()
    assert len(mgr2._containers) == 0
    assert len(mgr2._allocated_ports) == 0

    # Manager 2 retrieves desktop by employee ID via DB recovery
    recovered = await mgr2.get_desktop_by_employee_id(
        emp.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo
    )
    assert recovered is not None
    assert recovered["docker_id"] == spec1["docker_id"]
    assert recovered["vnc_port"] == 5901
    assert recovered["novnc_port"] == 6081

    # Verify status query also recovers from DB
    status_rec = await mgr2.get_desktop_status(spec1["docker_id"], container_repo=container_repo)
    assert status_rec is not None
    assert status_rec["status"] == ContainerStatus.RUNNING.value

    # Verify port allocation with used_ports avoids collision
    used = {c.vnc_port for c in await container_repo.list_all() if c.status == ContainerStatus.RUNNING}
    v_new, nv_new = mgr2.allocate_ports("emp-new-02", used_vnc_ports=used)
    assert v_new == 5902
    assert nv_new == 6082

    # Cleanup
    await mgr2.terminate_desktop(spec1["docker_id"], container_repo=container_repo)
    if os.path.exists(spec1["workspace"]):
        shutil.rmtree(spec1["workspace"], ignore_errors=True)


@pytest.mark.asyncio
async def test_employee_factory_preserves_skill_levels(async_session: AsyncSession):
    """
    Verifies that EmployeeFactory parses and sets correct SkillLevel (expert/advanced)
    from role definitions rather than defaulting everything to intermediate.
    """
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    dev = await factory.hire_employee(
        name="Elena Rostova",
        title="Senior Backend Engineer",
        role_id="senior-backend-engineer"
    )

    skills_dict = {s.skill_name: s for s in dev.skills}
    assert "Python" in skills_dict
    assert skills_dict["Python"].level == SkillLevel.EXPERT
    assert "FastAPI" in skills_dict
    assert skills_dict["FastAPI"].level == SkillLevel.ADVANCED
    assert "Redis" in skills_dict
    assert skills_dict["Redis"].level == SkillLevel.INTERMEDIATE


def test_dockerfile_employee_base_security_non_root():
    """
    FR-4.3 & NFR-1: Validates that Dockerfile.employee-base declares non-root user (USER employee),
    pre-creates isolated workspace directory, and chowns /home/employee before ENTRYPOINT.
    """
    df_path = os.path.abspath("docker/Dockerfile.employee-base")
    assert os.path.exists(df_path), "Dockerfile.employee-base must exist"
    with open(df_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Must specify USER employee at the end
    assert "USER employee" in content
    assert "USER root" in content
    # USER employee must come AFTER the last USER root
    last_root_pos = content.rfind("USER root")
    last_emp_pos = content.rfind("USER employee")
    assert last_emp_pos > last_root_pos, "Dockerfile must end in USER employee (uid 1000) for unprivileged execution"
    assert "/home/employee/workspace" in content
    assert "chown -R employee:employee" in content


# =====================================================================
# 4. FastAPI Endpoints & Workstation Route Integration Tests
# =====================================================================

@pytest.mark.asyncio
async def test_fastapi_phase1_comprehensive_api(async_session: AsyncSession):
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Health check
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "healthy"

        # Boss status
        res = await client.get("/boss/status")
        assert res.status_code == 200
        assert res.json()["ceo_status"] in {"ready", "rules_only"}
        assert res.json()["system_health"] == "degraded"

        # Objective submission validation: empty objective returns 400
        res_bad = await client.post("/boss/objective", json={"objective": "   "})
        assert res_bad.status_code == 400
        assert "cannot be empty" in res_bad.json()["detail"]

        # Objective submission with automatic workforce bootstrap
        res_obj = await client.post(
            "/boss/objective",
            json={
                "objective": "Build high-throughput payment processing engine",
                "project_name": "Project-Payments"
            }
        )
        assert res_obj.status_code == 200
        proj_data = res_obj.json()
        project_id = proj_data["id"]
        assert proj_data["name"] == "Project-Payments"

        # List projects
        res_projs = await client.get("/projects")
        assert res_projs.status_code == 200
        assert any(p["id"] == project_id for p in res_projs.json())

        # Get project tasks
        res_tasks = await client.get(f"/projects/{project_id}/tasks")
        assert res_tasks.status_code == 200
        tasks = res_tasks.json()
        assert len(tasks) >= 4
        first_task_id = tasks[0]["task_id"]

        # Get specific task by ID
        res_single_task = await client.get(f"/projects/{project_id}/tasks/{first_task_id}")
        assert res_single_task.status_code == 200
        assert res_single_task.json()["task_id"] == first_task_id

        # Boss dynamically recruits only because the empty roster lacks verified skills.
        res_emps = await client.get("/employees")
        assert res_emps.status_code == 200
        emp_list = res_emps.json()
        assert emp_list
        assert all(employee["employee_id"].startswith("ANC-EMP-") for employee in emp_list)
        assert all(employee["name"] not in {"Alex Morgan", "Maya Chen"} for employee in emp_list)
        assert any(task["status"] == "assigned" for task in tasks)

        audit_records = await AuditRepository(async_session).list_all()
        assert any(record.action == "SUBMIT_OBJECTIVE" for record in audit_records)
        staffing_decisions = [record for record in audit_records if record.action == "STAFFING_DECISION"]
        assert staffing_decisions
        assert staffing_decisions[0].actor == "BossAI"
        assert staffing_decisions[0].details["recruited_employee_ids"]

        # The owner cannot bypass Boss staffing by directly creating a staff record.
        direct_hire = await client.post(
            "/employees",
            json={"name": "Unauthorized Fictional Worker", "title": "Engineer"},
        )
        assert direct_hire.status_code == 405

        # 404 on non-existent employee
        res_404 = await client.get("/employees/ANC-EMP-NONEXISTENT")
        assert res_404.status_code == 404

        # Container lifecycle APIs require an owner approval ID and must not
        # create/delete live desktops as a side effect of this unit test.
        res_prov = await client.post("/employees/ANC-EMP-NONEXISTENT/workstation/provision")
        assert res_prov.status_code in {404, 422}
