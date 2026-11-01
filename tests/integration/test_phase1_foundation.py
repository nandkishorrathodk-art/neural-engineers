import os
import shutil

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.boss.brain import BossBrain
from neural.constants import ContainerStatus, ProjectStatus, TaskPriority, TaskStatus
from neural.containers.manager import ContainerManager
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.evaluation.verifier import IndependentVerifier
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_boss_brain_hierarchical_planning_and_db_persistence(async_session: AsyncSession):
    """
    FR-1 & Phase 1 Done Gate:
    CEO Brain analyzes objective, decomposes into DAG phases & tasks,
    and persists Project and Task rows into the database via repositories.
    """
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)

    # 1. Recruit an employee first so auto-assignment can trigger
    factory = EmployeeFactory(employee_repo)
    swe = await factory.hire_employee(
        name="Alex Morgan",
        title="Senior Backend Engineer",
        role_id="senior-backend-engineer"
    )

    brain = BossBrain(
        project_repo=project_repo,
        task_repo=task_repo,
        employee_repo=employee_repo
    )

    objective = "Build a cloud-native microservices architecture with automated CI/CD and monitoring"
    project, tasks = await brain.plan_and_create_project(
        objective=objective,
        project_name="Project-CloudNative",
        auto_assign_first_task=True
    )

    # Verify Project
    assert project.id is not None
    assert project.name == "Project-CloudNative"
    assert project.objective == objective
    assert project.status == ProjectStatus.PLANNING
    assert project.execution_plan is not None
    assert len(project.execution_plan["phases"]) >= 3

    # Verify Tasks persisted in DB
    assert len(tasks) >= 4
    persisted_tasks = await task_repo.list_by_project(project.id)
    assert len(persisted_tasks) == len(tasks)

    # Verify First Task was assigned to our recruited employee
    first_task = tasks[0]
    assert first_task.assigned_to == swe.id
    assert first_task.status == TaskStatus.ASSIGNED
    assert first_task.priority == TaskPriority.HIGH

    # Verify remaining tasks are pending
    for t in tasks[1:]:
        assert t.project_id == project.id
        assert t.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_container_workstation_lifecycle_and_isolation(async_session: AsyncSession):
    """
    FR-4 & Phase 1 Deliverables:
    Docker Workstation Manager allocates sequential VNC (5901+) and noVNC (6081+) ports,
    creates isolated workspace directories on host, and enforces resource bounds and non-root execution.
    """
    container_repo = ContainerRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(employee_repo)

    emp1 = await factory.hire_employee(name="Alice", title="Backend Engineer")
    emp2 = await factory.hire_employee(name="Bob", title="QA Engineer")

    manager = ContainerManager()

    # Provision first employee workstation
    spec1 = await manager.provision_employee_desktop(
        employee_id=emp1.employee_id,
        role_image="employee-swe",
        cpu_limit=2.0,
        mem_limit="4g",
        container_repo=container_repo,
        employee_repo=employee_repo
    )

    assert spec1["vnc_port"] == 5901
    assert spec1["novnc_port"] == 6081
    assert "http://localhost:6081/vnc.html" in spec1["desktop_url"]
    assert spec1["user"] == "1000:1000"
    assert spec1["privileged"] is False
    assert spec1["pids_limit"] == 200
    assert spec1["cpu_limit"] == 2.0
    assert spec1["mem_limit"] == "4g"
    assert os.path.exists(spec1["workspace"])
    assert spec1["status"] == ContainerStatus.RUNNING.value

    # Provision second employee workstation (must receive distinct incremental ports)
    spec2 = await manager.provision_employee_desktop(
        employee_id=emp2.employee_id,
        role_image="employee-qa",
        container_repo=container_repo,
        employee_repo=employee_repo
    )

    assert spec2["vnc_port"] == 5902
    assert spec2["novnc_port"] == 6082
    assert "http://localhost:6082/vnc.html" in spec2["desktop_url"]
    assert spec2["workspace"] != spec1["workspace"]
    assert os.path.exists(spec2["workspace"])

    # Verify DB persistence of container records
    db_c1 = await container_repo.get_by_docker_id(spec1["docker_id"])
    assert db_c1 is not None
    assert db_c1.vnc_port == 5901
    assert db_c1.novnc_port == 6081

    # Verify employee linked to container
    reloaded_emp1 = await employee_repo.get_by_employee_id(emp1.employee_id)
    assert reloaded_emp1 is not None
    assert reloaded_emp1.container_id == db_c1.id

    # Terminate workstation 1 & verify ports released
    await manager.terminate_desktop(spec1["docker_id"], container_repo=container_repo)
    status1 = await manager.get_desktop_status(spec1["docker_id"])
    assert status1["status"] == ContainerStatus.TERMINATED.value

    # Clean up created test workspaces
    for spec in (spec1, spec2):
        if os.path.exists(spec["workspace"]):
            shutil.rmtree(spec["workspace"], ignore_errors=True)


@pytest.mark.asyncio
async def test_workforce_recruitment_and_skill_certification(async_session: AsyncSession):
    """
    FR-2, FR-3 & Phase 1 Deliverables:
    Initial workforce bootstrapping, unverified skills registration, task assignment,
    and empirical skill certification upon QA pass receipts.
    """
    employee_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    project_repo = ProjectRepository(async_session)
    factory = EmployeeFactory(employee_repo)

    # 1. Bootstrap initial workforce (Senior Backend + QA)
    workforce = await factory.bootstrap_initial_workforce()
    assert len(workforce) == 2

    backend_dev, qa_dev = workforce[0], workforce[1]
    assert backend_dev.name == "Alex Morgan"
    assert backend_dev.title == "Senior Backend Engineer"
    assert qa_dev.name == "Maya Chen"
    assert qa_dev.title == "Quality Assurance & Verification Engineer"

    # 2. Check base skills loaded from configs/role-definitions.yaml
    assert any(s.skill_name == "FastAPI" for s in backend_dev.skills)
    assert any(s.skill_name == "pytest" for s in qa_dev.skills)

    # 3. All skills initially unverified
    assert all(not s.verified for s in backend_dev.skills)
    assert all(not s.verified for s in qa_dev.skills)

    # 4. Create a Project and Task
    project = await project_repo.create(name="Project-Alpha", objective="Implement Auth Module")
    task = await task_repo.create(
        task_id="TASK-AUTH-001",
        project_id=project.id,
        title="Design JWT Authentication",
        objective="Implement OAuth2/JWT token issuance",
        scope="Auth Module",
        acceptance_criteria="Passing pytest test suite",
        required_capabilities=["Python", "FastAPI"]
    )

    # 5. Assign task to backend developer
    await task_repo.assign_task(task.task_id, backend_dev.employee_id)
    assigned_tasks = await task_repo.list_by_assignee(backend_dev.employee_id)
    assert len(assigned_tasks) == 1
    assert assigned_tasks[0].task_id == "TASK-AUTH-001"
    assert assigned_tasks[0].status == TaskStatus.ASSIGNED

    # 6. Independent QA verification certifies skills
    verifier = IndependentVerifier(employee_repo)
    result = await verifier.verify_task_result(
        task_id=task.task_id,
        employee_id=backend_dev.employee_id,
        artifacts=["/workspace/auth.py", "/workspace/test_auth.py"],
        test_exit_code=0,
        tested_skills=["FastAPI", "Python"]
    )

    assert result["passed"] is True
    reloaded_dev = await employee_repo.get_by_employee_id(backend_dev.employee_id)
    assert reloaded_dev is not None
    verified_skills = {s.skill_name: s for s in reloaded_dev.skills if s.verified}
    assert "FastAPI" in verified_skills
    assert "Python" in verified_skills
    assert "TASK-AUTH-001" in verified_skills["FastAPI"].evidence_task_ids


@pytest.mark.asyncio
async def test_fastapi_api_endpoints_seamless_integration(async_session: AsyncSession):
    """
    Phase 1 FastAPI Endpoints Verification:
    /health, /boss, /projects, /employees, /meetings
    """
    app = create_app()

    # Override database dependency with test session
    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Health Endpoint
        res = await client.get("/health/")
        assert res.status_code == 200
        health_data = res.json()
        assert health_data["status"] == "healthy"
        assert "ANCOS" in health_data["service"]

        # 2. Boss Status Endpoint
        res = await client.get("/boss/status")
        assert res.status_code == 200
        boss_data = res.json()
        assert boss_data["ceo_status"] == "operational"

        # 3. Employee Recruitment Endpoint
        res = await client.post(
            "/employees/",
            json={
                "name": "Devin Torres",
                "title": "Senior Backend Engineer",
                "department": "Engineering",
                "role_id": "senior-backend-engineer",
                "skills": ["Python", "FastAPI", "Docker"]
            }
        )
        assert res.status_code == 200
        emp_data = res.json()
        assert emp_data["name"] == "Devin Torres"
        assert emp_data["employee_id"].startswith("ANC-EMP-")
        assert len(emp_data["skills"]) == 3

        # 4. List Employees Endpoint
        res = await client.get("/employees/")
        assert res.status_code == 200
        employees_list = res.json()
        assert len(employees_list) >= 1
        assert any(e["name"] == "Devin Torres" for e in employees_list)

        # 5. Submit Objective Endpoint (Creates project, plans DAG, creates tasks, assigns first task)
        res = await client.post(
            "/boss/objective",
            json={
                "objective": "Design and build an enterprise knowledge graph microservice",
                "project_name": "Project-KnowledgeGraph"
            }
        )
        assert res.status_code == 200
        proj_data = res.json()
        project_id = proj_data["id"]
        assert proj_data["name"] == "Project-KnowledgeGraph"
        assert "phases" in proj_data["execution_plan"]

        # 6. List Projects Endpoint
        res = await client.get("/projects/")
        assert res.status_code == 200
        projects_list = res.json()
        assert any(p["id"] == project_id for p in projects_list)

        # 7. Get Project by ID
        res = await client.get(f"/projects/{project_id}")
        assert res.status_code == 200
        single_proj = res.json()
        assert single_proj["id"] == project_id

        # 8. Get Project Tasks
        res = await client.get(f"/projects/{project_id}/tasks")
        assert res.status_code == 200
        tasks_list = res.json()
        assert len(tasks_list) >= 4
        # First task should be assigned
        assert tasks_list[0]["status"] == "assigned"
        assert tasks_list[0]["assigned_to"] is not None

        # 9. Schedule Boardroom Meeting
        res = await client.post(
            "/meetings/schedule",
            json={
                "title": "Phase 1 Architecture & Foundation Review",
                "meeting_type": "project_review",
                "agenda": ["Review Docker Desktop Workstation", "Review CEO Planner", "Verify Skills"],
                "participants": ["BossAI", emp_data["employee_id"]]
            }
        )
        assert res.status_code == 200
        meeting_data = res.json()
        assert meeting_data["title"] == "Phase 1 Architecture & Foundation Review"
        assert meeting_data["meeting_id"].startswith("ANC-MTG-")
        assert len(meeting_data["agenda"]) == 3

        # 10. List Meetings
        res = await client.get("/meetings/")
        assert res.status_code == 200
        meetings_list = res.json()
        assert len(meetings_list) >= 1
        assert any(m["title"] == "Phase 1 Architecture & Foundation Review" for m in meetings_list)

        # 11. Error path: Non-existent Project returns 404
        bad_res = await client.get("/projects/non-existent-uuid-1234")
        assert bad_res.status_code == 404
        assert bad_res.json()["detail"] == "Project not found"

        # 12. Error path: Non-existent Project tasks returns 404
        bad_tasks_res = await client.get("/projects/non-existent-uuid-1234/tasks")
        assert bad_tasks_res.status_code == 404
        assert bad_tasks_res.json()["detail"] == "Project not found"


@pytest.mark.asyncio
async def test_phase1_edge_cases(async_session: AsyncSession):
    """
    Edge case tests for Phase 1:
    - Planning and persisting when zero employees exist (tasks remain pending).
    - Port allocation fragmentation and reclamation.
    - Employee hiring with unknown role_id fallback.
    """
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)

    # 1. Zero employees exist: auto_assign should safely leave task pending
    brain = BossBrain(project_repo=project_repo, task_repo=task_repo, employee_repo=employee_repo)
    project, tasks = await brain.plan_and_create_project(
        objective="Edge case objective with empty roster",
        auto_assign_first_task=True
    )
    assert project.id is not None
    assert len(tasks) >= 4
    assert tasks[0].assigned_to is None
    assert tasks[0].status == TaskStatus.PENDING

    # 2. Unknown role_id fallback in EmployeeFactory
    factory = EmployeeFactory(employee_repo)
    unknown_emp = await factory.hire_employee(
        name="Sam Specialist",
        title="Custom Specialist",
        role_id="non-existent-role-slug"
    )
    assert unknown_emp.title == "Custom Specialist"
    assert unknown_emp.department == "Engineering"
    assert len(unknown_emp.skills) >= 1

    # 3. Port allocation fragmentation and reclamation
    manager = ContainerManager()
    p1 = manager.allocate_ports("id-1")
    p2 = manager.allocate_ports("id-2")
    p3 = manager.allocate_ports("id-3")
    assert p1 == (5901, 6081)
    assert p2 == (5902, 6082)
    assert p3 == (5903, 6083)

    # Release the middle port (id-2)
    manager.release_ports("id-2")
    # Next allocation should reuse the released port 5902/6082
    p4 = manager.allocate_ports("id-4")
    assert p4 == (5902, 6082)

    # Clean up
    for cid in ("id-1", "id-3", "id-4"):
        manager.release_ports(cid)
