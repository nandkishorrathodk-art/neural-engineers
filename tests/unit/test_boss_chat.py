import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.api.schemas.requests import BossChatRequest
from neural.api.schemas.responses import BossChatResponse
from neural.boss.brain import BossBrain
from neural.constants import EmployeeStatus, TaskPriority, TaskStatus
from neural.db.repositories import (
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.llm.gateway import LLMGateway


def test_boss_chat_request_response_schemas():
    req = BossChatRequest(message="What is the company status?")
    assert req.message == "What is the company status?"
    assert req.conversation_history == []
    assert req.project_id is None

    res = BossChatResponse(
        reply="Company operations are running at full capacity.",
        action={"type": "view_status"},
        plan_proposal={
            "title": "Alpha Launch",
            "objective": "Launch Alpha",
            "estimated_cost_usd": 25.0,
            "phases": [{"name": "Phase 1", "tasks": ["Task 1"]}],
            "required_skills": ["python"],
            "deadline_days": 5,
        },
        conversation_history=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ],
    )
    assert res.reply.startswith("Company operations")
    assert res.plan_proposal["title"] == "Alpha Launch"
    assert len(res.conversation_history) == 2


@pytest.mark.asyncio
async def test_boss_chat_grounded_company_status(async_session):
    emp_repo = EmployeeRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)

    # Provision sample company truth
    emp = await emp_repo.create(
        employee_id="ANC-EMP-ARCH",
        name="Elena Rostova",
        title="Lead Architect",
        department="Engineering",
        personality="Analytical",
        system_prompt="You are Lead Architect Elena Rostova.",
    )
    await emp_repo.add_skill(emp.employee_id, "python", "expert", verified=True)
    await emp_repo.update_status(emp.employee_id, EmployeeStatus.WORKING)

    project = await proj_repo.create(
        name="Project Apex",
        objective="Deploy distributed cluster",
    )

    t1 = await task_repo.create(
        task_id="TASK-CHAT-01",
        project_id=project.id,
        title="Provision Consensus Cluster",
        objective="Spin up Raft consensus nodes",
        scope="Cluster nodes",
        acceptance_criteria="Raft nodes active",
        priority=TaskPriority.HIGH,
        assigned_to=emp.employee_id,
    )
    await task_repo.update_status(t1.task_id, TaskStatus.RUNNING)

    t2 = await task_repo.create(
        task_id="TASK-CHAT-02",
        project_id=project.id,
        title="Verify Cryptographic Ledger",
        objective="Pass independent verifier receipt",
        scope="Audit trails",
        acceptance_criteria="Valid receipt",
        priority=TaskPriority.MEDIUM,
        assigned_to=emp.employee_id,
    )
    t2.verification_result = {"passed": True, "notes": "All cryptographic signatures valid"}
    await async_session.commit()
    await task_repo.update_status(t2.task_id, TaskStatus.COMPLETED)

    brain = BossBrain(
        project_repo=proj_repo,
        task_repo=task_repo,
        employee_repo=emp_repo,
    )

    result = await brain.chat(
        message="Give me an executive briefing on company status and deliverables",
        db_session=async_session,
    )

    assert "reply" in result
    reply = result["reply"]
    # Check that factual grounding contains live state
    assert "ANC-EMP-ARCH" in reply or "Elena Rostova" in reply or "Workforce" in reply
    assert "Verified Deliverables" in reply or "deliverable" in reply.lower()
    assert "TASK-CHAT-02" in reply
    assert result["plan_proposal"] is None
    assert len(result["conversation_history"]) == 2
    assert result["conversation_history"][0]["role"] == "user"
    assert result["conversation_history"][1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_boss_chat_active_blockers_inquiry(async_session):
    emp_repo = EmployeeRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)

    emp = await emp_repo.create(
        employee_id="ANC-EMP-BACKEND",
        name="Marcus Vance",
        title="Backend Engineer",
        department="Engineering",
        system_prompt="You are Backend Engineer Marcus Vance.",
    )

    project = await proj_repo.create(
        name="Project Gateway",
        objective="Build API Gateway",
    )

    blocked_task = await task_repo.create(
        task_id="TASK-CHAT-BLK-01",
        project_id=project.id,
        title="Configure TLS Termination",
        objective="Setup certificate management",
        scope="TLS setup",
        acceptance_criteria="TLS certs active",
        assigned_to=emp.employee_id,
    )
    await task_repo.update_status(blocked_task.task_id, TaskStatus.BLOCKED)
    blocked_task.retry_count = 3
    await async_session.commit()

    brain = BossBrain(
        project_repo=proj_repo,
        task_repo=task_repo,
        employee_repo=emp_repo,
    )

    result = await brain.chat(
        message="Are there any active blockers or stalled issues right now?",
        db_session=async_session,
    )

    reply = result["reply"]
    assert "blocked task(s)" in reply.lower() or "active blockers" in reply.lower()
    assert blocked_task.task_id in reply
    assert "Configure TLS Termination" in reply


@pytest.mark.asyncio
async def test_boss_chat_plan_proposal_generation(async_session):
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)

    brain = BossBrain(
        project_repo=proj_repo,
        task_repo=task_repo,
        employee_repo=emp_repo,
    )

    result = await brain.chat(
        message="Plan and build a real-time WebSocket notifications service with message queues",
        db_session=async_session,
    )

    assert result["plan_proposal"] is not None
    plan = result["plan_proposal"]
    assert "title" in plan
    assert "phases" in plan
    assert len(plan["phases"]) >= 2
    assert "required_skills" in plan
    assert plan["estimated_cost_usd"] > 0
    assert result["action"] is not None
    assert result["action"]["type"] == "launch_project_dag"
    assert "🚀 Launch Project DAG" in result["reply"] or "Launch" in result["reply"]


@pytest.mark.asyncio
async def test_boss_chat_history_maintenance(async_session):
    brain = BossBrain()
    initial_history = [
        {"role": "user", "content": "Hello Boss", "timestamp": "2026-11-11T10:00:00Z"},
        {"role": "assistant", "content": "Greetings Operator.", "timestamp": "2026-11-11T10:00:05Z"},
    ]

    result = await brain.chat(
        message="What are our core priorities today?",
        conversation_history=initial_history,
        db_session=async_session,
    )

    updated_history = result["conversation_history"]
    assert len(updated_history) == 4
    assert updated_history[0]["content"] == "Hello Boss"
    assert updated_history[1]["content"] == "Greetings Operator."
    assert updated_history[2]["content"] == "What are our core priorities today?"
    assert updated_history[3]["role"] == "assistant"
    assert updated_history[3]["content"] == result["reply"]


@pytest.mark.asyncio
async def test_boss_chat_with_mock_llm_json_response(async_session):
    invoker_called = False

    async def mock_provider_invoker(**kwargs):
        nonlocal invoker_called
        invoker_called = True
        return {
            "content": (
                "I have conducted an architectural review for your proposed security scanner service.\n\n"
                "```json\n"
                "{\n"
                '  "plan_proposal": {\n'
                '    "title": "Automated Vulnerability Scanner",\n'
                '    "objective": "Scan container images and dependencies for known CVEs",\n'
                '    "estimated_cost_usd": 20.0,\n'
                '    "phases": [\n'
                '      {"name": "Phase 1: CVE Database Sync", "tasks": ["Sync NVD database", "Parse CVSS scores"]},\n'
                '      {"name": "Phase 2: Static Container Analysis", "tasks": ["Trivy wrapper", "Report generation"]}\n'
                '    ],\n'
                '    "required_skills": ["python", "security", "docker"],\n'
                '    "deadline_days": 4\n'
                "  },\n"
                '  "action": {\n'
                '    "type": "launch_project_dag",\n'
                '    "objective": "Scan container images and dependencies for known CVEs",\n'
                '    "project_name": "Automated Vulnerability Scanner"\n'
                "  }\n"
                "}\n"
                "```"
            ),
            "input_tokens": 120,
            "output_tokens": 150,
            "provider": "openai",
        }

    gateway = LLMGateway(provider_invoker=mock_provider_invoker)
    brain = BossBrain(llm_gateway=gateway)
    # Ensure planner uses configured provider check
    brain.planner.llm_gateway = gateway

    result = await brain.chat(
        message="Please plan a container vulnerability scanner service",
        db_session=async_session,
    )

    assert result["plan_proposal"] is not None
    assert result["plan_proposal"]["title"] == "Automated Vulnerability Scanner"
    assert len(result["plan_proposal"]["phases"]) == 2
    assert "CVE Database Sync" in result["plan_proposal"]["phases"][0]["name"]
    assert "```json" not in result["reply"]  # Clean markdown without raw json code block


@pytest.mark.asyncio
async def test_boss_chat_endpoint_http(async_session):
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # 1. Test status discussion
        res1 = await client.post(
            "/boss/chat",
            json={
                "message": "What is our current monthly budget and available quota?",
                "conversation_history": [],
            },
        )
        assert res1.status_code == 200, res1.text
        data1 = res1.json()
        assert "reply" in data1
        assert "Budget" in data1["reply"] or "Monthly" in data1["reply"] or "Financial" in data1["reply"]
        assert len(data1["conversation_history"]) == 2

        # 2. Test mission planning via HTTP
        res2 = await client.post(
            "/boss/chat",
            json={
                "message": "Plan a high-throughput event processing pipeline with Kafka and ClickHouse",
                "conversation_history": data1["conversation_history"],
            },
        )
        assert res2.status_code == 200, res2.text
        data2 = res2.json()
        assert data2["plan_proposal"] is not None
        assert "title" in data2["plan_proposal"]
        assert len(data2["plan_proposal"]["phases"]) >= 2
        assert data2["action"] is not None
        assert len(data2["conversation_history"]) == 4

        # 3. Test validation error on empty message
        res3 = await client.post(
            "/boss/chat",
            json={"message": ""},
        )
        assert res3.status_code == 422

        # 4. Test validation error on whitespace-only message
        res4 = await client.post(
            "/boss/chat",
            json={"message": "   "},
        )
        assert res4.status_code == 400
        assert res4.json()["detail"] == "Message cannot be empty"


@pytest.mark.asyncio
async def test_boss_chat_preserves_code_snippets_while_extracting_plan(async_session):
    async def mock_provider_invoker(**kwargs):
        return {
            "content": (
                "Here is the database schema definition:\n\n"
                "```sql\n"
                "CREATE TABLE event_logs (\n"
                "    id UUID PRIMARY KEY,\n"
                "    payload JSONB NOT NULL\n"
                ");\n"
                "```\n\n"
                "And here is the proposed execution plan:\n\n"
                "```json\n"
                "{\n"
                '  "plan_proposal": {\n'
                '    "title": "Event Store Service",\n'
                '    "objective": "Build event store service",\n'
                '    "estimated_cost_usd": 18.0,\n'
                '    "phases": [\n'
                '      {"name": "Phase 1: DB Schema", "tasks": ["Migration", "Indices"]}\n'
                '    ],\n'
                '    "required_skills": ["postgresql", "sql"]\n'
                "  }\n"
                "}\n"
                "```"
            ),
            "input_tokens": 100,
            "output_tokens": 150,
            "provider": "openai",
        }

    gateway = LLMGateway(provider_invoker=mock_provider_invoker)
    brain = BossBrain(llm_gateway=gateway)
    brain.planner.llm_gateway = gateway

    result = await brain.chat(
        message="Plan an event store with PostgreSQL",
        db_session=async_session,
    )

    assert result["plan_proposal"] is not None
    assert result["plan_proposal"]["title"] == "Event Store Service"
    # The SQL code snippet must NOT be wiped out!
    assert "```sql" in result["reply"]
    assert "CREATE TABLE event_logs" in result["reply"]
    # The JSON plan proposal code block must be stripped from conversational text
    assert '"plan_proposal"' not in result["reply"]


@pytest.mark.asyncio
async def test_boss_chat_normalizes_partial_llm_plan_proposal(async_session):
    async def mock_provider_invoker(**kwargs):
        return {
            "content": (
                "I have outlined the high-level architecture.\n\n"
                "```json\n"
                "{\n"
                '  "plan_proposal": {\n'
                '    "title": "Minimalist Cache Layer"\n'
                "  }\n"
                "}\n"
                "```"
            ),
            "input_tokens": 80,
            "output_tokens": 80,
            "provider": "openai",
        }

    gateway = LLMGateway(provider_invoker=mock_provider_invoker)
    brain = BossBrain(llm_gateway=gateway)
    brain.planner.llm_gateway = gateway

    result = await brain.chat(
        message="Build a redis cache layer for token verification",
        db_session=async_session,
    )

    plan = result["plan_proposal"]
    assert plan is not None
    assert plan["title"] == "Minimalist Cache Layer"
    # Ensure missing objective is defaulted to the user prompt
    assert plan["objective"] == "Build a redis cache layer for token verification"
    # Ensure phases and skills are normalized with defaults
    assert len(plan["phases"]) >= 1
    assert "required_skills" in plan and len(plan["required_skills"]) > 0
    assert plan["estimated_cost_usd"] > 0
    assert plan["deadline_days"] > 0
    # Ensure action is automatically provided
    assert result["action"] is not None
    assert result["action"]["type"] == "launch_project_dag"
    assert result["action"]["objective"] == plan["objective"]


@pytest.mark.asyncio
async def test_boss_chat_reports_active_emergency_stop(async_session):
    from neural.db.repositories import EmergencyStopRepository

    # Activate emergency halt state in DB via repository
    repo = EmergencyStopRepository(async_session)
    await repo.set_state(
        active=True,
        actor="OwnerGate",
        reason="Security anomaly detected in workstation container",
    )
    await async_session.commit()

    brain = BossBrain()
    result = await brain.chat(
        message="What is our current operational status and are there any issues?",
        db_session=async_session,
    )

    reply = result["reply"]
    assert "EMERGENCY STOP" in reply or "HALTED" in reply
    assert "Security anomaly detected in workstation container" in reply
    assert "OwnerGate" in reply


@pytest.mark.asyncio
async def test_boss_chat_handles_corrupted_history_entries(async_session):
    brain = BossBrain()
    corrupted_history = [
        {"invalid": "no role or content"},
        "plain string instead of dict",
        None,
        {"role": "user", "content": "Prior message"},
    ]

    result = await brain.chat(
        message="What is the workforce roster?",
        conversation_history=corrupted_history,
        db_session=async_session,
    )

    assert "reply" in result
    assert len(result["conversation_history"]) >= 2
    # Verify corrupted non-dict items were filtered out
    for h in result["conversation_history"]:
        assert isinstance(h, dict)
        assert "role" in h and "content" in h
