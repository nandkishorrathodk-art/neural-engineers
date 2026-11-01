from datetime import UTC, datetime, timedelta

import pytest
from neural.constants import ActionItemStatus, MeetingStatus, MeetingType, TaskStatus
from neural.db.repositories import (
    ActionItemRepository,
    ApprovalRepository,
    EmployeeRepository,
    MeetingRepository,
    PerformanceRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.exceptions import FabricatedProgressError
from neural.meetings.actions import ActionItemTracker
from neural.meetings.briefings import ExecutiveBriefingGenerator
from neural.meetings.conductor import MeetingConductor
from neural.meetings.minutes import MeetingMinutesGenerator
from neural.meetings.participants import ParticipantSelector
from neural.meetings.reports import ProgressReportCompiler, validate_report_data
from neural.meetings.scheduler import MeetingScheduler
from sqlalchemy.ext.asyncio import AsyncSession


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_participant_selector_daily_standup(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    await factory.hire_employee(name="Devin", title="Backend Engineer", role_id="senior-backend-engineer")
    await factory.hire_employee(name="Alice", title="Frontend Engineer", initial_skills=["React"])
    await factory.hire_employee(name="Bob", title="QA Engineer", role_id="qa-engineer")

    selector = ParticipantSelector(employee_repo=emp_repo)
    participants = await selector.select_participants(MeetingType.DAILY_STANDUP)

    assert "BossAI" in participants
    assert len(participants) == 4  # BossAI + 3 hired engineers
    assert any(p.startswith("ANC-EMP-") for p in participants)


@pytest.mark.asyncio
async def test_participant_selector_incident_review(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    backend = await factory.hire_employee(name="Backend Dev", title="Backend Engineer")
    qa = await factory.hire_employee(name="QA Dev", title="Quality Assurance Engineer")
    await factory.hire_employee(name="UI Dev", title="UI Designer")


    project = await proj_repo.create(name="Project Incident", objective="Fix critical bug")
    task = await task_repo.create(
        task_id="TASK-INC-01",
        project_id=project.id,
        title="Payment Service Gateway",
        objective="Process payments",
        scope="Backend",
        acceptance_criteria="Pass tests",
        status=TaskStatus.BLOCKED,
        assigned_to=backend.id
    )

    selector = ParticipantSelector(employee_repo=emp_repo, task_repo=task_repo)
    participants = await selector.select_participants(
        MeetingType.INCIDENT_REVIEW,
        task_ids=[task.task_id]
    )

    assert "BossAI" in participants
    # Must include the assignee of the blocked task and QA engineer
    assert backend.employee_id in participants
    assert qa.employee_id in participants


@pytest.mark.asyncio
async def test_participant_selector_executive_briefing(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    await factory.hire_employee(name="Lead Architect", title="Principal System Architect")
    await factory.hire_employee(name="Junior Dev", title="Junior Engineer")

    selector = ParticipantSelector(employee_repo=emp_repo)
    participants = await selector.select_participants(MeetingType.EXECUTIVE_BRIEFING)

    assert "BossAI" in participants
    assert "HumanOwner" in participants
    # Senior / Principal engineer is included
    all_emps = await emp_repo.list_all()
    lead_emp = next(e for e in all_emps if "Principal" in e.title)
    assert lead_emp.employee_id in participants


@pytest.mark.asyncio
async def test_participant_selector_profiles(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)
    emp = await factory.hire_employee(name="Sarah Connor", title="DevOps Engineer", department="Infrastructure")

    selector = ParticipantSelector(employee_repo=emp_repo)
    profiles = await selector.get_participant_profiles(["BossAI", "HumanOwner", emp.employee_id])

    assert len(profiles) == 3
    assert profiles[0]["name"] == "Central Brain (CEO)"
    assert profiles[1]["name"] == "Executive Board / Human Owner"
    assert profiles[2]["name"] == "Sarah Connor"
    assert profiles[2]["department"] == "Infrastructure"


def test_fact_grounded_report_valid_compilation():
    report = ProgressReportCompiler.compile(
        employee_id="ANC-EMP-001",
        task_id="TASK-01",
        what_was_done="Implemented JWT authentication handler and unit tests",
        what_remains="Implement refresh token rotation",
        what_is_blocking=None,
        execution_logs=["Running test suite...", "23 tests passed in 0.42s"],
        git_commits=["a1b2c3d: feat(auth): add JWT validator"],
        exit_code=0
    )

    assert report.employee_id == "ANC-EMP-001"
    assert report.task_id == "TASK-01"
    assert report.factual_status == "completed"
    assert report.verification_receipt["exit_code"] == 0
    assert len(report.verification_receipt["git_commits"]) == 1
    assert len(report.verification_receipt["execution_logs"]) == 2


def test_fact_grounded_report_enforces_4_questions():
    # 1. Missing what_was_done
    with pytest.raises(FabricatedProgressError) as exc_info:
        ProgressReportCompiler.compile(
            employee_id="ANC-EMP-001",
            task_id="TASK-01",
            what_was_done="",
            what_remains="Finish tests",
            exit_code=0
        )
    assert "What was done" in str(exc_info.value)

    # 2. Missing what_remains
    with pytest.raises(FabricatedProgressError) as exc_info:
        ProgressReportCompiler.compile(
            employee_id="ANC-EMP-001",
            task_id="TASK-01",
            what_was_done="Wrote code",
            what_remains="  ",
            exit_code=0
        )
    assert "What remains" in str(exc_info.value)

    # 3. Missing verification receipt
    with pytest.raises(FabricatedProgressError) as exc_info:
        ProgressReportCompiler.compile(
            employee_id="ANC-EMP-001",
            task_id="TASK-01",
            what_was_done="Wrote code",
            what_remains="Deploy",
            verification_receipt={}
        )
    assert "verification receipt" in str(exc_info.value).lower()



def test_fact_grounded_report_rejects_fabricated_percentages():
    # Attempting to supply unbacked percentage metric
    payload = {
        "employee_id": "ANC-EMP-001",
        "task_id": "TASK-01",
        "what_was_done": "Refactored router modules",
        "what_remains": "Write integration tests",
        "what_is_blocking": None,
        "progress_percentage": 75,  # Fabricated!
        "verification_receipt": {"exit_code": 0}
    }

    with pytest.raises(FabricatedProgressError) as exc_info:
        validate_report_data(payload)
    assert "Fabricated progress percentage" in str(exc_info.value)

    # Attempting to claim completed with non-zero exit code
    payload2 = {
        "employee_id": "ANC-EMP-001",
        "task_id": "TASK-01",
        "what_was_done": "Tried to build docker container",
        "what_remains": "Fix build failure",
        "what_is_blocking": "Dockerfile syntax error",
        "factual_status": "completed",  # False claim!
        "verification_receipt": {"exit_code": 1}
    }
    with pytest.raises(FabricatedProgressError) as exc_info:
        validate_report_data(payload2)
    assert "exit code 1" in str(exc_info.value)


@pytest.mark.asyncio
async def test_action_item_tracker_lifecycle_and_overdue(async_session: AsyncSession):
    meeting_repo = MeetingRepository(async_session)
    action_repo = ActionItemRepository(async_session)
    tracker = ActionItemTracker(action_repo)

    meeting = await meeting_repo.create(
        meeting_id="ANC-MTG-ACT1",
        title="Sprint Sync",
        meeting_type=MeetingType.DAILY_STANDUP
    )

    # 1. Create open action item with past deadline
    past_due = utc_now() - timedelta(hours=2)
    item = await tracker.create_action_item(
        meeting_id=meeting.id,
        description="Fix database migration index",
        assigned_to="ANC-EMP-001",
        deadline=past_due
    )
    assert item.status == ActionItemStatus.OPEN
    assert tracker.is_overdue(item) is True

    # 2. Check overdue query
    overdue_list = await tracker.list_overdue()
    assert len(overdue_list) == 1
    assert overdue_list[0].id == item.id

    # 3. Resolve item
    resolved = await tracker.resolve_action_item(item.id)
    assert resolved.status == ActionItemStatus.RESOLVED
    assert tracker.is_overdue(resolved) is False

    # 4. Overdue query should now be empty
    overdue_list = await tracker.list_overdue()
    assert len(overdue_list) == 0


@pytest.mark.asyncio
async def test_action_item_automatic_carry_over(async_session: AsyncSession):
    meeting_repo = MeetingRepository(async_session)
    action_repo = ActionItemRepository(async_session)
    tracker = ActionItemTracker(action_repo)

    # Meeting 1
    m1 = await meeting_repo.create(
        meeting_id="ANC-MTG-M1",
        title="Day 1 Standup",
        meeting_type=MeetingType.DAILY_STANDUP
    )
    # 1 resolved item, 1 open item
    item_resolved = await tracker.create_action_item(
        meeting_id=m1.id,
        description="Setup Docker workspace",
        assigned_to="ANC-EMP-001"
    )
    await tracker.resolve_action_item(item_resolved.id)

    await tracker.create_action_item(
        meeting_id=m1.id,
        description="Write OpenAPI contract for payments",
        assigned_to="ANC-EMP-002"
    )


    # Meeting 2
    m2 = await meeting_repo.create(
        meeting_id="ANC-MTG-M2",
        title="Day 2 Standup",
        meeting_type=MeetingType.DAILY_STANDUP
    )

    # Carry over unresolved items from M1 to M2
    carried = await tracker.carry_over_unresolved_items(to_meeting_id=m2.id, from_meeting_id=m1.id)
    assert len(carried) == 1
    assert "OpenAPI contract for payments" in carried[0].description
    assert carried[0].assigned_to == "ANC-EMP-002"
    assert carried[0].meeting_id == m2.id

    # Running carry over again should not duplicate
    carried_second = await tracker.carry_over_unresolved_items(to_meeting_id=m2.id, from_meeting_id=m1.id)
    assert len(carried_second) == 0


def test_meeting_minutes_generator_and_markdown():
    decisions = [
        {
            "topic": "Storage Engine Selection",
            "decision": "Adopt PostgreSQL with asyncpg",
            "rationale": "High write throughput and relational ACID guarantees for billing",
            "decided_by": "BossAI",
            "impact": "Architectural standard"
        }
    ]
    reports = [
        {
            "employee_id": "ANC-EMP-001",
            "task_id": "TASK-DB-01",
            "what_was_done": "Created initial Alembic migration scripts",
            "what_remains": "Execute test migration in sandbox",
            "what_is_blocking": None,
            "verification_receipt": {"exit_code": 0, "git_commits": ["c8d9e0f"]}
        }
    ]

    minutes = MeetingMinutesGenerator.generate_minutes(
        meeting_id="ANC-MTG-001",
        title="Architecture Decision Board",
        meeting_type=MeetingType.ARCHITECTURE_REVIEW,
        participants=["BossAI", "ANC-EMP-001"],
        agenda=["Select DB", "Review migrations"],
        progress_reports=reports,
        decisions=decisions
    )

    assert minutes["meeting_id"] == "ANC-MTG-001"
    assert len(minutes["decisions"]) == 1
    assert minutes["decisions"][0]["topic"] == "Storage Engine Selection"
    assert "PostgreSQL" in minutes["decisions"][0]["decision"]
    assert minutes["total_decisions"] == 1

    md = MeetingMinutesGenerator.format_as_markdown(minutes)
    assert "# 🏛️ Meeting Minutes: Architecture Decision Board" in md
    assert "Storage Engine Selection" in md
    assert "Adopt PostgreSQL with asyncpg" in md
    assert "c8d9e0f" in md


@pytest.mark.asyncio
async def test_meeting_scheduler_triggers(async_session: AsyncSession):
    meeting_repo = MeetingRepository(async_session)
    emp_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    await factory.hire_employee(name="Alice", title="Backend Engineer")
    await factory.hire_employee(name="Bob", title="QA Engineer")

    selector = ParticipantSelector(emp_repo, task_repo)
    conductor = MeetingConductor(meeting_repo)
    scheduler = MeetingScheduler(
        meeting_repo=meeting_repo,
        conductor=conductor,
        participant_selector=selector,
        task_repo=task_repo
    )

    # 1. Scheduled trigger: Daily Standup
    standup = await scheduler.trigger_daily_standup()
    assert standup.meeting_type == MeetingType.DAILY_STANDUP
    assert standup.status == MeetingStatus.SCHEDULED
    assert len(standup.participants) >= 2

    # 2. Event-based trigger: Task Blocked
    blocker_mtg = await scheduler.on_task_blocked(
        task_id="TASK-99",
        blocker_reason="Redis connection refused on port 6379"
    )
    assert blocker_mtg.meeting_type == MeetingType.INCIDENT_REVIEW
    assert "TASK-99" in blocker_mtg.title
    assert any("Redis connection refused" in item for item in blocker_mtg.agenda)

    # 3. Event-based trigger: Phase Completed
    phase_mtg = await scheduler.on_phase_completed(
        project_id="PROJ-01",
        phase_id="PHASE-01"
    )
    assert phase_mtg.meeting_type == MeetingType.PROJECT_REVIEW
    assert "PHASE-01" in phase_mtg.title


@pytest.mark.asyncio
async def test_executive_briefing_generator(async_session: AsyncSession):
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)
    meeting_repo = MeetingRepository(async_session)
    perf_repo = PerformanceRepository(async_session)
    app_repo = ApprovalRepository(async_session)
    action_repo = ActionItemRepository(async_session)

    # 1. Setup workforce & skills
    factory = EmployeeFactory(emp_repo)
    emp = await factory.hire_employee(name="Devin", title="Backend Engineer", initial_skills=["Python", "FastAPI"])
    await emp_repo.verify_skill(emp.employee_id, "Python", "TASK-01")

    # 2. Setup project & tasks
    proj = await proj_repo.create(name="Project Zeus", objective="Build high-frequency exchange")
    await task_repo.create(
        task_id="TASK-Z-01",
        project_id=proj.id,
        title="Order Matching Engine",
        objective="Process orders",
        scope="Core",
        acceptance_criteria="Tests pass",
        status=TaskStatus.COMPLETED
    )
    await task_repo.create(
        task_id="TASK-Z-02",
        project_id=proj.id,
        title="Market Data Stream",
        objective="WebSocket stream",
        scope="Core",
        acceptance_criteria="Tests pass",
        status=TaskStatus.BLOCKED
    )

    # 3. Performance records & spend
    await perf_repo.record(
        employee_id=emp.employee_id,
        task_id="TASK-Z-01",
        cost_tokens=5000,
        cost_usd=0.075
    )

    # 4. Pending Approval
    await app_repo.create(
        action_type="PRODUCTION_MIGRATION",
        description="Migrate live database cluster",
        justification="Zero-downtime cutover needed",
        requested_by=emp.employee_id
    )

    generator = ExecutiveBriefingGenerator(
        project_repo=proj_repo,
        task_repo=task_repo,
        employee_repo=emp_repo,
        meeting_repo=meeting_repo,
        perf_repo=perf_repo,
        approval_repo=app_repo,
        action_item_repo=action_repo
    )

    briefing = await generator.generate_briefing(project_id=proj.id)

    assert briefing["generated_at"] is not None
    assert len(briefing["milestones"]) == 1
    m = briefing["milestones"][0]
    assert m["total_tasks"] == 2
    assert m["completed_tasks"] == 1
    assert m["blocked_tasks"] == 1
    assert m["completion_percentage"] == 50.0  # Mathematically grounded

    assert briefing["active_workforce"]["total_employees"] == 1
    assert briefing["active_workforce"]["skills"]["total_verified"] == 1

    assert len(briefing["blockers"]) == 1
    assert briefing["blockers"][0]["task_id"] == "TASK-Z-02"

    assert briefing["financial_costs"]["total_usd"] == 0.075
    assert briefing["financial_costs"]["total_tokens"] == 5000

    assert len(briefing["pending_approvals"]) == 1
    assert briefing["pending_approvals"][0]["action_type"] == "PRODUCTION_MIGRATION"

    # Markdown format
    md = await generator.generate_markdown_briefing(project_id=proj.id)
    assert "# 🏢 ANCOS Autonomous Executive Briefing" in md
    assert "Project Zeus" in md
    assert "50.0%" in md
    assert "$0.0750 USD" in md


@pytest.mark.asyncio
async def test_action_item_carry_over_multi_meeting_resolution(async_session: AsyncSession):
    """
    Verifies that when an action item is carried across multiple meetings and resolved in M2,
    all predecessor instances are resolved and subsequent meetings (M3) do NOT re-carry it.
    """
    m_repo = MeetingRepository(async_session)
    a_repo = ActionItemRepository(async_session)
    tracker = ActionItemTracker(a_repo)

    m1 = await m_repo.create(meeting_id="ANC-MTG-SEQ1", title="Standup Day 1", meeting_type=MeetingType.DAILY_STANDUP)
    m2 = await m_repo.create(meeting_id="ANC-MTG-SEQ2", title="Standup Day 2", meeting_type=MeetingType.DAILY_STANDUP)
    m3 = await m_repo.create(meeting_id="ANC-MTG-SEQ3", title="Standup Day 3", meeting_type=MeetingType.DAILY_STANDUP)

    item1 = await tracker.create_action_item(
        meeting_id=m1.id,
        description="Migrate Redis cluster to Sentinel",
        assigned_to="ANC-EMP-001"
    )
    assert item1.status == ActionItemStatus.OPEN

    # Carry over to M2
    carried_m2 = await tracker.carry_over_unresolved_items(to_meeting_id=m2.id)
    assert len(carried_m2) == 1
    item2 = carried_m2[0]
    assert "[Carried Over] Migrate Redis cluster to Sentinel" in item2.description

    # Resolve in M2
    await tracker.resolve_action_item(item2.id)

    # Predecessor item1 in M1 must also be resolved
    item1_reloaded = await a_repo.get_by_id(item1.id)
    assert item1_reloaded.status == ActionItemStatus.RESOLVED

    # Now M3 runs carry over: must carry 0 items because the deliverable was completed!
    carried_m3 = await tracker.carry_over_unresolved_items(to_meeting_id=m3.id)
    assert len(carried_m3) == 0


@pytest.mark.asyncio
async def test_action_item_deadline_parsing_and_tz_overdue(async_session: AsyncSession):
    """
    Verifies string ISO deadline parsing, timezone-aware comparison, and overdue calculation.
    """
    m_repo = MeetingRepository(async_session)
    a_repo = ActionItemRepository(async_session)
    tracker = ActionItemTracker(a_repo)

    meeting = await m_repo.create(meeting_id="ANC-MTG-DL1", title="DL Test", meeting_type=MeetingType.DAILY_STANDUP)

    # 1. Past deadline as ISO string
    past_str = "2024-01-01T00:00:00Z"
    item_past = await tracker.create_action_item(
        meeting_id=meeting.id,
        description="Past item",
        assigned_to="ANC-EMP-001",
        deadline=past_str  # type: ignore[arg-type]
    )
    assert item_past.deadline is not None
    assert tracker.is_overdue(item_past) is True

    # 2. Future deadline as timezone-aware datetime
    future_tz = datetime.now(UTC) + timedelta(days=5)
    item_future = await tracker.create_action_item(
        meeting_id=meeting.id,
        description="Future item",
        assigned_to="ANC-EMP-002",
        deadline=future_tz
    )
    assert tracker.is_overdue(item_future) is False


def test_fact_grounded_report_text_percentage_mismatch_rejected():
    """
    Ensures that claiming a percentage in text that mismatches the empirical receipt is rejected.
    """
    # Mismatched claim: text claims 90% complete, but receipts show only 2/10 subtasks (20%)
    mismatched_payload = {
        "employee_id": "ANC-EMP-001",
        "task_id": "TASK-01",
        "what_was_done": "Completed 90% of the payment migration modules",
        "what_remains": "Remaining tasks",
        "verification_receipt": {
            "exit_code": 0,
            "subtasks_total": 10,
            "subtasks_done": 2  # 20%, NOT 90%!
        }
    }
    with pytest.raises(FabricatedProgressError) as exc_info:
        validate_report_data(mismatched_payload)
    assert "does not match receipts" in str(exc_info.value) or "Fabricated" in str(exc_info.value)

    # Matching claim: text claims 20% complete and receipt matches 2/10 (20%)
    valid_payload = {
        "employee_id": "ANC-EMP-001",
        "task_id": "TASK-01",
        "what_was_done": "Completed 20% of the payment migration modules",
        "what_remains": "Remaining tasks",
        "verification_receipt": {
            "exit_code": 0,
            "subtasks_total": 10,
            "subtasks_done": 2
        }
    }
    validate_report_data(valid_payload)  # Should not raise


def test_fact_grounded_report_empty_evidence_rejected():
    """
    Ensures that empty receipts or null exit codes are rejected.
    """
    # Null exit_code with no other evidence
    with pytest.raises(FabricatedProgressError):
        validate_report_data({
            "employee_id": "ANC-EMP-001",
            "task_id": "TASK-01",
            "what_was_done": "Worked on auth",
            "what_remains": "Finish tests",
            "verification_receipt": {"exit_code": None}
        })

    # Empty git_commits list with no other evidence
    with pytest.raises(FabricatedProgressError):
        validate_report_data({
            "employee_id": "ANC-EMP-001",
            "task_id": "TASK-01",
            "what_was_done": "Worked on auth",
            "what_remains": "Finish tests",
            "verification_receipt": {"git_commits": []}
        })


@pytest.mark.asyncio
async def test_meeting_conductor_cannot_reconvene_concluded(async_session: AsyncSession):
    """
    Ensures that once a meeting is concluded, it cannot be accidentally reconvened.
    """
    m_repo = MeetingRepository(async_session)
    conductor = MeetingConductor(m_repo)

    meeting = await conductor.schedule_meeting(
        title="Sync",
        meeting_type=MeetingType.DAILY_STANDUP,
        agenda=["Agenda 1"],
        participants=["BossAI"]
    )
    await conductor.convene_meeting(meeting.meeting_id)
    await conductor.conclude_meeting(meeting.meeting_id)

    # Attempting to convene again must raise ValueError
    with pytest.raises(ValueError) as exc_info:
        await conductor.convene_meeting(meeting.meeting_id)
    assert "already concluded" in str(exc_info.value)
