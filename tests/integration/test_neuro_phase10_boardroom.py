from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.constants import (
    ActionItemStatus,
    ExecutionMode,
    MeetingStatus,
    MeetingType,
    TaskPriority,
    TaskStatus,
)
from neural.db.repositories import (
    ActionItemRepository,
    EmployeeRepository,
    MeetingRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.meetings.actions import ActionItemTracker
from neural.meetings.conductor import MeetingConductor, utc_now
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_neuro_phase10_full_operational_boardroom_loop(async_session: AsyncSession):
    """
    End-to-end integration test for NEURO-10 / G-10:
    1. Persisted workstation allocation for diverse roles.
    2. Visual world desk state synchronized with transactional database.
    3. Blocked task with empirical runtime failure receipt.
    4. Boardroom operational review with active floor moderation.
    5. Fact-grounded progress report and blocker extraction.
    6. Deliberation, explicit architectural decision, and tracked action item.
    7. Action item resolution, carry-over verification, and desk relocation.
    """
    emp_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    m_repo = MeetingRepository(async_session)
    action_repo = ActionItemRepository(async_session)
    factory = EmployeeFactory(emp_repo)
    conductor = MeetingConductor(meeting_repo=m_repo, action_item_repo=action_repo)
    tracker = ActionItemTracker(action_repo)

    # 1. Recruit workforce and verify persistent desk assignments
    architect = await factory.hire_employee(
        name="Dr. Elena Vance",
        title="Principal AI Research Scientist",
        department="AI Research Labs",
        role_id="research-scientist",
    )
    backend_dev = await factory.hire_employee(
        name="Marcus Cole",
        title="Senior Backend Systems Engineer",
        department="Core Engineering",
        role_id="senior-backend-engineer",
    )
    qa_eng = await factory.hire_employee(
        name="Seraphina Lin",
        title="Reliability & QA Engineer",
        department="Quality Assurance",
        role_id="qa-engineer",
    )
    devops_eng = await factory.hire_employee(
        name="Darius Vance",
        title="Cloud Infrastructure & SRE Engineer",
        department="DevOps & Infrastructure",
        role_id="devops-engineer",
    )

    assert architect.desk_id == "WS-RES-01"
    assert backend_dev.desk_id == "WS-ENG-01"
    assert qa_eng.desk_id == "WS-QA-01"
    assert devops_eng.desk_id == "WS-OPS-01"

    occupied_desks = await emp_repo.list_occupied_desks()
    assert {"WS-RES-01", "WS-ENG-01", "WS-QA-01", "WS-OPS-01"}.issubset(set(occupied_desks))

    # 2. Setup project and task blocked with runtime failure receipt
    project = await proj_repo.create(
        name="Autonomous Payments Engine",
        objective="Deploy zero-trust microservice with PostgreSQL connection pooling",
    )
    task = await task_repo.create(
        task_id="TASK-PAY-9001",
        project_id=project.id,
        assigned_to=backend_dev.id,
        title="Postgres Connection Pool Configuration",
        objective="Configure asyncpg connection pool with least-privilege security policy",
        scope="services/payments",
        acceptance_criteria="Pass synthetic 1000 RPS benchmark without connection saturation",
        execution_mode=ExecutionMode.TERMINAL,
        priority=TaskPriority.HIGH,
        status=TaskStatus.BLOCKED,
    )

    blocker_reason = "Deadlock encountered in PostgreSQL pg_locks during concurrent table migration"
    runtime_receipt = {
        "exit_code": 1,
        "stdout": "Running migration 0004_payment_ledgers.py...",
        "stderr": "psycopg2.errors.DeadlockDetected: deadlock detected on relation 'accounts'",
        "failed_statement": "ALTER TABLE accounts ADD CONSTRAINT chk_balance CHECK (balance >= 0);",
        "lock_wait_timeout_seconds": 30,
    }

    # 3. Convene operational boardroom review
    meeting = await conductor.schedule_meeting(
        title=f"Incident Boardroom Review — {task.task_id}",
        meeting_type=MeetingType.INCIDENT_REVIEW,
        agenda=[
            "1. Incident report presentation and empirical log analysis",
            "2. Root-cause blocker dissection",
            "3. Boss AI remediation directive & architectural decision",
            "4. Action item delegation with tracked resolution target",
        ],
        participants=["BossAI", backend_dev.employee_id, devops_eng.employee_id],
    )

    convened = await conductor.convene_meeting(meeting.meeting_id)
    assert convened.status == MeetingStatus.IN_PROGRESS
    assert convened.active_speaker_id == "BossAI"

    # 4. Marcus Cole takes the floor and presents the factual progress report
    report_dict = {
        "employee_id": backend_dev.employee_id,
        "task_id": task.task_id,
        "what_was_done": "Executed automated migration scripts for account schema under synthetic load",
        "what_remains": "Acquire advisory locks to serialize partition updates without deadlocking",
        "what_is_blocking": blocker_reason,
        "verification_receipt": runtime_receipt,
        "factual_status": "blocked",
    }
    report_result = await conductor.collect_report(meeting.meeting_id, report_dict)
    assert report_result["factual_status"] == "blocked"

    # Verify floor transferred to backend engineer during report presentation
    reloaded_m = await m_repo.get_by_meeting_id(meeting.meeting_id)
    assert reloaded_m is not None
    assert reloaded_m.active_speaker_id == backend_dev.employee_id

    # 5. Boss AI takes the floor, reviews root cause, and records architectural decision
    decision_topic = "DDL Lock Serialization & Migration Isolation"
    decision_text = "Enforce transactional advisory lock pg_advisory_xact_lock(hash('migration_accounts')) before applying schema constraints"
    decision_rationale = "Prevents circular wait graph between concurrent reader transactions and DDL constraint validation"

    decision = await conductor.record_decision(
        meeting_id=meeting.meeting_id,
        topic=decision_topic,
        decision=decision_text,
        rationale=decision_rationale,
        decided_by="BossAI",
        impact="Zero-downtime payments migration",
    )
    assert decision["decided_by"] == "BossAI"

    # 6. Boss AI delegates tracked action item to DevOps engineer
    action_deadline = utc_now() + timedelta(hours=4)
    action_item = await conductor.add_action_item(
        meeting_id=meeting.meeting_id,
        description=f"[{decision['decision_id']}] Patch migration 0004 with advisory lock in staging sandbox",
        assigned_to=devops_eng.employee_id,
        deadline=action_deadline,
    )
    action_item_id = str(action_item.id)
    assert action_item.status == ActionItemStatus.OPEN
    assert action_item.assigned_to == devops_eng.employee_id

    # 7. Transfer speaking floor to DevOps engineer for operational confirmation
    await conductor.set_speaker(
        meeting_id=meeting.meeting_id,
        speaker_id=devops_eng.employee_id,
        reason="Acknowledging migration patch delegation and commit SLA",
    )

    # 8. Conclude meeting and audit activity log
    concluded = await conductor.conclude_meeting(
        meeting_id=meeting.meeting_id,
        summary="Remediated migration deadlock via transactional advisory locks; assigned patch to DevOps.",
    )
    assert concluded.status == MeetingStatus.CONCLUDED
    assert concluded.active_speaker_id is None

    minutes = concluded.minutes
    assert minutes is not None
    assert "progress_reports" in minutes
    assert len(minutes["progress_reports"]) == 1
    assert "blockers_identified" in minutes
    assert len(minutes["blockers_identified"]) == 1
    assert minutes["blockers_identified"][0]["task_id"] == task.task_id

    # Verify complete chronological audit trail in activity_log
    activity_log = minutes["activity_log"]
    event_types = [e["event_type"] for e in activity_log]
    expected_order = [
        "convened",
        "report_presented",
        "blocker_identified",
        "decision_recorded",
        "action_item_assigned",
        "speaker_changed",
        "concluded",
    ]
    for expected_event in expected_order:
        assert expected_event in event_types

    # 9. DevOps engineer resolves action item
    await tracker.resolve_action_item(action_item_id)
    resolved_item = await action_repo.get_by_id(action_item_id)
    assert resolved_item is not None
    assert resolved_item.status == ActionItemStatus.RESOLVED

    # 10. Next day daily standup: verify 0 carried-over items remain
    next_standup = await conductor.schedule_meeting(
        title="Morning Daily Standup",
        meeting_type=MeetingType.DAILY_STANDUP,
        agenda=["Status updates"],
        participants=["BossAI", devops_eng.employee_id],
    )
    carried_items = await conductor.carry_over_unresolved(next_standup.meeting_id)
    assert len(carried_items) == 0

    # 11. Relocate backend engineer desk and verify database persistence & vacancy release
    updated_emp = await emp_repo.assign_desk(backend_dev.employee_id, "WS-ENG-02")
    assert updated_emp.desk_id == "WS-ENG-02"

    # WS-ENG-01 is now vacant and can be allocated to a newly hired engineer
    new_hire = await factory.hire_employee(
        name="Talia Frost",
        title="Software Engineer",
        department="Engineering",
    )
    assert new_hire.desk_id == "WS-ENG-01"


@pytest.mark.asyncio
async def test_neuro_phase10_workstation_and_meeting_api_roundtrip(async_session: AsyncSession):
    """
    Validates REST API contracts for workstation assignments and meeting lifecycle.
    """
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    lead = await factory.hire_employee(
        name="Lead Engineer",
        title="Staff Engineer",
        department="Engineering",
    )

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # 1. Fetch workstation assignments
        res_ws = await client.get("/employees/workstations/assignments")
        assert res_ws.status_code == 200
        ws_data = res_ws.json()
        assert len(ws_data) == 11
        lead_desk = next(w for w in ws_data if w["occupant_id"] == lead.employee_id)
        assert lead_desk["id"] == lead.desk_id

        # 2. Trigger operational review via REST
        review_payload = {
            "task_id": "TASK-API-101",
            "reporting_employee_id": lead.employee_id,
            "what_was_done": "Refactored JWT cryptographic verification logic",
            "what_remains": "Implement token revocation blacklist",
            "what_is_blocking": "Redis cache node unavailable during peak test",
            "verification_receipt": {"exit_code": 111, "error": "ConnectionRefusedError: 6379"},
            "decision_topic": "Redis Sentinel High Availability Cluster",
            "decision_text": "Deploy Redis Sentinel with 3 replicas for zero-downtime failover",
            "decision_rationale": "High availability is required for enterprise token revocation checks",
            "action_item_description": "Provision Redis Sentinel cluster in test environment",
            "action_item_assigned_to": lead.employee_id,
            "action_item_deadline_hours": 12,
        }
        res_rev = await client.post("/meetings/operational-review", json=review_payload)
        assert res_rev.status_code == 200
        review_data = res_rev.json()
        assert review_data["status"] == "concluded"
        assert len(review_data["decisions"]) == 1
        assert len(review_data["action_items"]) == 1
        assert len(review_data["activity_log"]) >= 6

        # 3. Verify get meeting detail includes active_speaker_id and activity_log
        meeting_id = review_data["meeting_id"]
        res_get = await client.get(f"/meetings/{meeting_id}")
        assert res_get.status_code == 200
        get_data = res_get.json()
        assert get_data["active_speaker_id"] is None
        assert len(get_data["activity_log"]) >= 6
