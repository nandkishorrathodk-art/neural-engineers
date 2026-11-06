from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.constants import ActionItemStatus, MeetingStatus, MeetingType
from neural.db.repositories import (
    ActionItemRepository,
    EmployeeRepository,
    MeetingRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.meetings.conductor import MeetingConductor, utc_now
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.asyncio
async def test_meeting_convene_sets_speaker_and_activity_log(async_session: AsyncSession):
    m_repo = MeetingRepository(async_session)
    conductor = MeetingConductor(meeting_repo=m_repo)

    meeting = await conductor.schedule_meeting(
        title="Sprint Sync",
        meeting_type=MeetingType.DAILY_STANDUP,
        agenda=["Status updates", "Blockers"],
        participants=["BossAI", "ANC-EMP-101"],
    )

    convened = await conductor.convene_meeting(meeting.meeting_id)
    assert convened.status == MeetingStatus.IN_PROGRESS
    assert convened.active_speaker_id == "BossAI"
    assert convened.minutes is not None
    assert "activity_log" in convened.minutes
    activity = convened.minutes["activity_log"]
    assert len(activity) >= 1
    assert activity[0]["event_type"] == "convened"
    assert activity[0]["speaker_id"] == "BossAI"


@pytest.mark.asyncio
async def test_action_item_api_rejects_reopening_resolved_item(async_session: AsyncSession):
    meeting_repo = MeetingRepository(async_session)
    action_repo = ActionItemRepository(async_session)
    meeting = await meeting_repo.create(
        meeting_id="ANC-MTG-ACTION-API-STATE",
        title="Action item state guard",
        meeting_type=MeetingType.DAILY_STANDUP,
    )
    item = await action_repo.create(
        meeting_id=meeting.id,
        description="Publish the verified handover",
        assigned_to="ANC-EMP-API-STATE",
    )
    await action_repo.update_status(item.id, ActionItemStatus.RESOLVED)

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.patch(
            f"/meetings/action-items/{item.id}",
            json={"status": ActionItemStatus.OPEN.value},
        )

    assert response.status_code == 409
    assert "cannot transition from 'resolved' to 'open'" in response.json()["detail"]
    reloaded = await action_repo.get_by_id(item.id)
    assert reloaded is not None and reloaded.status == ActionItemStatus.RESOLVED


@pytest.mark.asyncio
async def test_action_item_status_cas_rejects_a_stale_concurrent_transition(async_session: AsyncSession):
    meeting_repo = MeetingRepository(async_session)
    item = await ActionItemRepository(async_session).create(
        meeting_id=(await meeting_repo.create(
            meeting_id="ANC-MTG-ACTION-CAS",
            title="Concurrent action transition",
            meeting_type=MeetingType.DAILY_STANDUP,
        )).id,
        description="Complete the security review",
        assigned_to="ANC-EMP-CAS",
    )
    second_session_factory = async_sessionmaker(
        bind=async_session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with second_session_factory() as stale_session:
        stale_repo = ActionItemRepository(stale_session)
        stale_item = await stale_repo.get_by_id(item.id)
        assert stale_item is not None and stale_item.status == ActionItemStatus.OPEN

        await ActionItemRepository(async_session).update_status(item.id, ActionItemStatus.RESOLVED)

        with pytest.raises(ValueError, match="status changed concurrently"):
            await stale_repo.update_item(item.id, status=ActionItemStatus.IN_PROGRESS)

    current = await ActionItemRepository(async_session).get_by_id(item.id)
    assert current is not None and current.status == ActionItemStatus.RESOLVED


@pytest.mark.asyncio
async def test_meeting_set_speaker_changes_floor_and_records_activity(async_session: AsyncSession):
    m_repo = MeetingRepository(async_session)
    conductor = MeetingConductor(meeting_repo=m_repo)

    meeting = await conductor.schedule_meeting(
        title="Floor Transfer Sync",
        meeting_type=MeetingType.DAILY_STANDUP,
        agenda=["Intro", "Employee presentation"],
        participants=["BossAI", "ANC-EMP-202"],
    )
    await conductor.convene_meeting(meeting.meeting_id)

    updated = await conductor.set_speaker(
        meeting_id=meeting.meeting_id,
        speaker_id="ANC-EMP-202",
        reason="Presenting architecture proposal",
    )
    assert updated.active_speaker_id == "ANC-EMP-202"
    log = updated.minutes["activity_log"]
    speaker_change = [e for e in log if e["event_type"] == "speaker_changed"]
    assert len(speaker_change) == 1
    assert speaker_change[0]["speaker_id"] == "ANC-EMP-202"
    assert speaker_change[0]["data"]["previous_speaker_id"] == "BossAI"
    assert speaker_change[0]["data"]["reason"] == "Presenting architecture proposal"

    # Non-existent meeting raises ValueError
    with pytest.raises(ValueError, match="not found"):
        await conductor.set_speaker(meeting_id="ANC-MTG-INVALID", speaker_id="BossAI")


@pytest.mark.asyncio
async def test_collect_report_sets_speaker_and_logs_blocker(async_session: AsyncSession):
    m_repo = MeetingRepository(async_session)
    conductor = MeetingConductor(meeting_repo=m_repo)

    meeting = await conductor.schedule_meeting(
        title="Incident Sync",
        meeting_type=MeetingType.INCIDENT_REVIEW,
        agenda=["Incident triage"],
        participants=["BossAI", "ANC-EMP-303"],
    )
    await conductor.convene_meeting(meeting.meeting_id)

    report_payload = {
        "employee_id": "ANC-EMP-303",
        "task_id": "TASK-BLOCK-01",
        "what_was_done": "Investigated database latency spikes",
        "what_remains": "Apply connection pool configuration",
        "what_is_blocking": "PostgreSQL connection pool exhausted under synthetic load",
        "verification_receipt": {
            "exit_code": 1,
            "error": "Connection pool exhausted",
            "active_connections": 100,
        },
        "factual_status": "blocked",
    }

    report = await conductor.collect_report(meeting.meeting_id, report_payload)
    assert report["factual_status"] == "blocked"

    # Speaker should now be the reporting employee
    reloaded = await m_repo.get_by_meeting_id(meeting.meeting_id)
    assert reloaded is not None
    assert reloaded.active_speaker_id == "ANC-EMP-303"

    log = reloaded.minutes["activity_log"]
    assert any(e["event_type"] == "report_presented" for e in log)
    blocker_event = next(e for e in log if e["event_type"] == "blocker_identified")
    assert blocker_event["data"]["task_id"] == "TASK-BLOCK-01"
    assert "PostgreSQL connection pool" in blocker_event["data"]["blocker"]

    # Blockers identified list in minutes
    assert "blockers_identified" in reloaded.minutes
    assert len(reloaded.minutes["blockers_identified"]) == 1
    assert reloaded.minutes["blockers_identified"][0]["task_id"] == "TASK-BLOCK-01"


@pytest.mark.asyncio
async def test_record_decision_sets_speaker_and_logs_decision(async_session: AsyncSession):
    m_repo = MeetingRepository(async_session)
    conductor = MeetingConductor(meeting_repo=m_repo)

    meeting = await conductor.schedule_meeting(
        title="Architecture Decision",
        meeting_type=MeetingType.ARCHITECTURE_REVIEW,
        agenda=["Pool tuning"],
        participants=["BossAI", "ANC-EMP-404"],
    )
    await conductor.convene_meeting(meeting.meeting_id)

    dec = await conductor.record_decision(
        meeting_id=meeting.meeting_id,
        topic="Database Connection Pool",
        decision="Increase pool_size=20, max_overflow=30, and pool_timeout=15s",
        rationale="Eliminates exhaustion errors under burst traffic while respecting memory budget",
        decided_by="BossAI",
        impact="Production database stability",
    )

    reloaded = await m_repo.get_by_meeting_id(meeting.meeting_id)
    assert reloaded is not None
    assert reloaded.active_speaker_id == "BossAI"
    assert len(reloaded.decisions) == 1
    assert reloaded.decisions[0]["decision_id"] == dec["decision_id"]

    log = reloaded.minutes["activity_log"]
    dec_event = next(e for e in log if e["event_type"] == "decision_recorded")
    assert dec_event["speaker_id"] == "BossAI"
    assert "Database Connection Pool" in dec_event["summary"]


@pytest.mark.asyncio
async def test_conclude_meeting_resets_speaker_and_preserves_activity_log(async_session: AsyncSession):
    m_repo = MeetingRepository(async_session)
    a_repo = ActionItemRepository(async_session)
    conductor = MeetingConductor(meeting_repo=m_repo, action_item_repo=a_repo)

    meeting = await conductor.schedule_meeting(
        title="Wrapup Meeting",
        meeting_type=MeetingType.DAILY_STANDUP,
        agenda=["Wrapup"],
        participants=["BossAI", "ANC-EMP-505"],
    )
    await conductor.convene_meeting(meeting.meeting_id)
    await conductor.add_action_item(
        meeting_id=meeting.meeting_id,
        description="Verify database pool change in staging",
        assigned_to="ANC-EMP-505",
        deadline=utc_now() + timedelta(hours=12),
    )

    concluded = await conductor.conclude_meeting(
        meeting_id=meeting.meeting_id,
        summary="Sprint wrapup and action item distribution",
    )
    assert concluded.status == MeetingStatus.CONCLUDED
    assert concluded.active_speaker_id is None
    assert concluded.minutes is not None
    assert "activity_log" in concluded.minutes

    log = concluded.minutes["activity_log"]
    event_types = [e["event_type"] for e in log]
    assert "convened" in event_types
    assert "action_item_assigned" in event_types
    assert "concluded" in event_types


@pytest.mark.asyncio
async def test_conduct_operational_review_end_to_end(async_session: AsyncSession):
    m_repo = MeetingRepository(async_session)
    a_repo = ActionItemRepository(async_session)
    conductor = MeetingConductor(meeting_repo=m_repo, action_item_repo=a_repo)

    review = await conductor.conduct_operational_review(
        task_id="TASK-PROD-99",
        reporting_employee_id="ANC-EMP-ENGINEER-1",
        what_was_done="Analyzed Docker socket isolation vulnerability in workstation broker",
        what_remains="Implement restricted brokered proxy daemon",
        what_is_blocking="Restricted broker lacks socket privilege drop policy",
        verification_receipt={"exit_code": 2, "failed_check": "cap_drop_incomplete"},
        decision_topic="Container Least Privilege Hardening",
        decision_text="Enforce cap_drop=['ALL'] and no-new-privileges in RestrictedDockerBroker",
        decision_rationale="Zero trust container containment mandates complete drop of Linux capabilities",
        action_item_description="Configure RestrictedDockerBroker to drop all capabilities and test runner",
        action_item_assigned_to="ANC-EMP-ENGINEER-1",
        action_item_deadline=utc_now() + timedelta(hours=8),
    )

    assert review.status == MeetingStatus.CONCLUDED
    assert review.active_speaker_id is None
    assert len(review.decisions) == 1
    assert "Container Least Privilege Hardening" in review.decisions[0]["topic"]

    minutes = review.minutes
    assert minutes is not None
    assert "progress_reports" in minutes
    assert len(minutes["progress_reports"]) == 1
    assert minutes["progress_reports"][0]["factual_status"] == "blocked"

    assert "blockers_identified" in minutes
    assert len(minutes["blockers_identified"]) == 1
    assert "Restricted broker lacks" in minutes["blockers_identified"][0]["blocker"]

    # Activity log sequence
    log = minutes["activity_log"]
    event_sequence = [e["event_type"] for e in log]
    assert "convened" in event_sequence
    assert "report_presented" in event_sequence
    assert "blocker_identified" in event_sequence
    assert "decision_recorded" in event_sequence
    assert "action_item_assigned" in event_sequence
    assert "concluded" in event_sequence


@pytest.mark.asyncio
async def test_boardroom_operational_review_api_routes(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    m_repo = MeetingRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(
        name="Review Lead",
        title="Site Reliability Engineer",
        department="Infrastructure",
    )

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # POST /meetings/operational-review
        rev_payload = {
            "task_id": "TASK-INFRA-404",
            "reporting_employee_id": emp.employee_id,
            "what_was_done": "Monitored memory consumption during synthetic load tests",
            "what_remains": "Tune container memory limits",
            "what_is_blocking": "Workstation container OOM killed under 2GB limit",
            "verification_receipt": {"exit_code": 137, "oom_killed": True},
            "decision_topic": "Workstation Container Memory Sizing",
            "decision_text": "Increase desktop workstation memory cap from 2GB to 4GB",
            "decision_rationale": "Desktop environment + browser requires 3GB baseline peak memory",
            "action_item_description": "Update Docker broker default memory limit to 4GB",
            "action_item_assigned_to": emp.employee_id,
        }
        res = await client.post("/meetings/operational-review", json=rev_payload)
        assert res.status_code == 200
        rev_data = res.json()
        assert rev_data["status"] == "concluded"
        assert len(rev_data["decisions"]) == 1
        assert "Workstation Container Memory Sizing" in rev_data["decisions"][0]["topic"]
        assert len(rev_data["activity_log"]) >= 5

        # Schedule and convene a live meeting to test POST /meetings/{meeting_id}/speaker
        conductor = MeetingConductor(meeting_repo=m_repo)
        live_m = await conductor.schedule_meeting(
            title="Live Boardroom Discussion",
            meeting_type=MeetingType.DAILY_STANDUP,
            agenda=["Agenda item 1"],
            participants=["BossAI", emp.employee_id],
        )
        await conductor.convene_meeting(live_m.meeting_id)

        # Update speaker via route
        spk_res = await client.post(
            f"/meetings/{live_m.meeting_id}/speaker",
            json={"speaker_id": emp.employee_id, "reason": "Handing over floor"},
        )
        assert spk_res.status_code == 200
        spk_data = spk_res.json()
        assert spk_data["active_speaker_id"] == emp.employee_id
