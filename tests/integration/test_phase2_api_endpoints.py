import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.constants import EmployeeStatus, TaskStatus
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository
from neural.employees.factory import EmployeeFactory
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_tasks_api_endpoints(async_session: AsyncSession):
    """
    Integration test for Tasks API routes:
    /tasks, /tasks/{task_id}, /tasks/{task_id}/assign, /tasks/{task_id}/status,
    /tasks/{task_id}/dependencies, /tasks/projects/{project_id}/dag,
    /tasks/projects/{project_id}/execute
    """
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    # Setup project, employee, and tasks in DB
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)

    factory = EmployeeFactory(emp_repo)
    emp = await factory.hire_employee(name="Taylor Swift", title="Lead Engineer")

    project = await proj_repo.create(name="Project-API-Tasks", objective="Build API")
    t1 = await task_repo.create(
        task_id="TASK-API-01",
        project_id=project.id,
        title="DB Design",
        objective="Design tables",
        scope="Database",
        acceptance_criteria="Schema defined",
        status=TaskStatus.PENDING
    )
    t2 = await task_repo.create(
        task_id="TASK-API-02",
        project_id=project.id,
        title="Service Layer",
        objective="Implement endpoints",
        scope="API",
        acceptance_criteria="Endpoints active",
        status=TaskStatus.PENDING
    )
    await task_repo.add_dependency(t2.task_id, t1.task_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. List tasks
        res = await client.get("/tasks")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 2

        # Filter by project
        res_proj = await client.get(f"/tasks?project_id={project.id}")
        assert res_proj.status_code == 200
        assert len(res_proj.json()) == 2

        # 2. Get single task
        res_t1 = await client.get(f"/tasks/{t1.task_id}")
        assert res_t1.status_code == 200
        assert res_t1.json()["task_id"] == t1.task_id

        # 3. Assign task to employee
        res_assign = await client.post(
            f"/tasks/{t1.task_id}/assign",
            json={"employee_id": emp.employee_id}
        )
        assert res_assign.status_code == 200
        assert res_assign.json()["status"] == "running"

        # 4. Status update validation
        # Legal: RUNNING -> COMPLETED
        res_status = await client.post(
            f"/tasks/{t1.task_id}/status",
            json={"status": "completed", "notes": "Done via API"}
        )
        assert res_status.status_code == 200
        assert res_status.json()["status"] == "completed"

        # Verify employee was reset to IDLE on COMPLETED status
        emp_after = await emp_repo.get_by_employee_id(emp.employee_id)
        assert emp_after.status.value == "idle"

        # Test assigning task to terminated employee is rejected with 400
        terminated_emp = await factory.hire_employee(name="Terminated Emp", title="Ex-Dev")
        await emp_repo.update_status(terminated_emp.employee_id, EmployeeStatus.TERMINATED)
        res_assign_term = await client.post(
            f"/tasks/{t2.task_id}/assign",
            json={"employee_id": terminated_emp.employee_id}
        )
        assert res_assign_term.status_code == 400
        assert "Cannot assign task to employee" in res_assign_term.json()["detail"]

        # Illegal: COMPLETED -> RUNNING (should be rejected with 400)
        res_illegal = await client.post(
            f"/tasks/{t1.task_id}/status",
            json={"status": "running"}
        )
        assert res_illegal.status_code == 400
        assert "Illegal task state transition" in res_illegal.json()["detail"]

        # 5. Dependencies endpoint
        res_deps = await client.get(f"/tasks/{t2.task_id}/dependencies")
        assert res_deps.status_code == 200
        deps_data = res_deps.json()
        assert t1.task_id in deps_data["prerequisites"]

        # 6. DAG endpoint
        res_dag = await client.get(f"/tasks/projects/{project.id}/dag")
        assert res_dag.status_code == 200
        dag_data = res_dag.json()
        assert dag_data["project_id"] == project.id
        assert len(dag_data["nodes"]) == 2
        assert dag_data["topological_order"] == [t1.task_id, t2.task_id]

        # 7. Execute DAG endpoint
        res_exec = await client.post(
            f"/tasks/projects/{project.id}/execute",
            json={"max_concurrency": 2}
        )
        assert res_exec.status_code == 200
        exec_data = res_exec.json()
        assert exec_data["success"] is True
        assert t2.task_id in exec_data["completed"]

        # 8. Test executing DAG with cyclic dependencies returns 400
        proj_cycle = await proj_repo.create(name="Project-Cycle", objective="Cycle test")
        tc1 = await task_repo.create(task_id="TC1", project_id=proj_cycle.id, title="C1", objective="o1", scope="s", acceptance_criteria="a")
        tc2 = await task_repo.create(task_id="TC2", project_id=proj_cycle.id, title="C2", objective="o2", scope="s", acceptance_criteria="a")
        await task_repo.add_dependency(tc2.task_id, tc1.task_id)
        await task_repo.add_dependency(tc1.task_id, tc2.task_id)

        res_cycle = await client.post(f"/tasks/projects/{proj_cycle.id}/execute")
        assert res_cycle.status_code == 400
        assert "Cannot execute DAG" in res_cycle.json()["detail"]


@pytest.mark.asyncio
async def test_llm_api_endpoints(async_session: AsyncSession):
    """
    Integration test for LLM telemetry and generation API routes:
    /llm/telemetry, /llm/routes, /llm/telemetry/projects/{id},
    /llm/telemetry/employees/{id}, /llm/telemetry/tasks/{id}, /llm/generate
    """
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Routes and tier configuration
        res_routes = await client.get("/llm/routes")
        assert res_routes.status_code == 200
        routes_data = res_routes.json()
        assert "tiers" in routes_data
        assert "routes" in routes_data
        assert "reasoning" in routes_data["tiers"]
        assert "coding" in routes_data["tiers"]

        # 2. Generate completion
        res_gen = await client.post(
            "/llm/generate",
            json={
                "prompt": "Hello world from ANCOS API",
                "tier": "fast",
                "project_id": "PROJ-API-TEST",
                "employee_id": "EMP-API-TEST",
                "task_id": "TASK-API-TEST"
            }
        )
        assert res_gen.status_code == 200
        gen_data = res_gen.json()
        assert "content" in gen_data
        assert gen_data["cost_usd"] >= 0

        # 3. Overall Telemetry
        res_telem = await client.get("/llm/telemetry")
        assert res_telem.status_code == 200
        telem_data = res_telem.json()
        assert telem_data["total_calls"] >= 1
        assert telem_data["total_tokens"] > 0

        # 4. Project Telemetry
        res_proj = await client.get("/llm/telemetry/projects/PROJ-API-TEST")
        assert res_proj.status_code == 200
        assert res_proj.json()["call_count"] >= 1

        # 5. Employee Telemetry
        res_emp = await client.get("/llm/telemetry/employees/EMP-API-TEST")
        assert res_emp.status_code == 200
        assert res_emp.json()["call_count"] >= 1


@pytest.mark.asyncio
async def test_comms_api_endpoints(async_session: AsyncSession):
    """
    Integration test for Comms API routes:
    /comms/broadcast, /comms/department/{dept}, /comms/direct,
    /comms/messages, /comms/channels
    """
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Channels list
        res_channels = await client.get("/comms/channels")
        assert res_channels.status_code == 200
        assert "boss:broadcast" in res_channels.json()["channels"]

        # 2. Executive Broadcast
        res_bcast = await client.post(
            "/comms/broadcast",
            json={
                "title": "API Launch",
                "content": "Phase 2 APIs are now live",
                "priority": "high"
            }
        )
        assert res_bcast.status_code == 200
        assert res_bcast.json()["status"] == "published"
        assert res_bcast.json()["channel"] == "boss:broadcast"
        assert "message_id" in res_bcast.json()

        # 3. Department Broadcast
        res_dept = await client.post(
            "/comms/department/engineering",
            json={
                "title": "Sprint Planning",
                "content": "Sprint begins Monday"
            }
        )
        assert res_dept.status_code == 200
        assert res_dept.json()["department"] == "engineering"
        assert res_dept.json()["channel"] == "dept:engineering:broadcast"

        # 4. Direct Message
        res_dm = await client.post(
            "/comms/direct",
            json={
                "sender": "CEO",
                "recipient": "EMP-LEAD",
                "content": "Great progress on Phase 2",
                "subject": "Appreciation"
            }
        )
        assert res_dm.status_code == 200
        assert res_dm.json()["status"] == "sent"
        assert res_dm.json()["channel"] == "employee:EMP-LEAD:inbox"

        # 5. List messages from persistent audit trail
        res_msgs = await client.get("/comms/messages")
        assert res_msgs.status_code == 200
        messages = res_msgs.json()
        assert len(messages) >= 3

        # Filter by department
        res_dept_msgs = await client.get("/comms/messages?department=engineering")
        assert res_dept_msgs.status_code == 200
        dept_msgs = res_dept_msgs.json()
        assert len(dept_msgs) >= 1
        assert dept_msgs[0]["channel"] == "dept:engineering:broadcast"

        # Filter by employee
        res_emp_msgs = await client.get("/comms/messages?employee_id=EMP-LEAD")
        assert res_emp_msgs.status_code == 200
        emp_msgs = res_emp_msgs.json()
        assert len(emp_msgs) >= 1
        assert emp_msgs[0]["recipient"] == "EMP-LEAD"
