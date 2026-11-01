from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
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
from neural.meetings.briefings import ExecutiveBriefingGenerator
from neural.meetings.conductor import MeetingConductor
from neural.meetings.participants import ParticipantSelector
from neural.meetings.reports import ProgressReportCompiler
from neural.meetings.scheduler import MeetingScheduler
from sqlalchemy.ext.asyncio import AsyncSession


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_phase3_full_boardroom_lifecycle(async_session: AsyncSession):
    """
    Phase 3 Master Done-Gate Integration Test:
    1. Multi-department workforce hired (Backend, Frontend, QA).
    2. Project created with tasks; task encounters blocker.
    3. MeetingScheduler autonomously convenes incident review.
    4. ParticipantSelector selects affected assignee, QA, and Boss.
    5. Fact-grounded reports submitted with empirical receipts.
    6. Rejection of fabricated progress percentages enforced.
    7. Boss records explicit decisions with rationales.
    8. Action items created with deadlines; session concludes with structured minutes.
    9. Next standup carries forward unresolved action items.
    10. Executive Briefing compiled for Human Owner with accurate spend, blockers, and milestones.
    """
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)
    meeting_repo = MeetingRepository(async_session)
    action_repo = ActionItemRepository(async_session)
    perf_repo = PerformanceRepository(async_session)
    app_repo = ApprovalRepository(async_session)

    # 1. Recruit Workforce
    factory = EmployeeFactory(emp_repo)
    backend = await factory.hire_employee(name="Alex Rivera", title="Senior Backend Engineer", role_id="senior-backend-engineer")
    frontend = await factory.hire_employee(name="Taylor Swift", title="Frontend Engineer", initial_skills=["React", "Next.js"])
    qa = await factory.hire_employee(name="Morgan Chen", title="Quality Assurance Engineer", role_id="qa-engineer")

    # 2. Setup Project & Tasks
    project = await proj_repo.create(
        name="Project-PaymentEngine",
        objective="Deploy resilient global payments processing system"
    )
    task1 = await task_repo.create(
        task_id="TASK-PAY-01",
        project_id=project.id,
        title="Stripe & Adyen Webhook Gateway",
        objective="Handle webhook events",
        scope="Backend Services",
        acceptance_criteria="Pass HMAC verification and idempotency tests",
        assigned_to=backend.id,
        status=TaskStatus.BLOCKED
    )
    task2 = await task_repo.create(
        task_id="TASK-PAY-02",
        project_id=project.id,
        title="Payment Status Dashboard",
        objective="Live operator payment stream",
        scope="Frontend UI",
        acceptance_criteria="E2E UI tests pass",
        assigned_to=frontend.id,
        status=TaskStatus.RUNNING
    )

    # Record some performance spend
    await perf_repo.record(
        employee_id=backend.employee_id,
        task_id=task1.task_id,
        cost_tokens=12500,
        cost_usd=0.1875
    )

    # Record a pending approval
    await app_repo.create(
        action_type="MERCHANT_API_KEY_ROTATION",
        description="Rotate primary production API secret for Stripe",
        justification="Mandatory compliance rotation",
        requested_by=backend.employee_id
    )

    # 3. MeetingScheduler triggers Blocker Sync
    conductor = MeetingConductor(meeting_repo, action_item_repo=action_repo)
    selector = ParticipantSelector(emp_repo, task_repo)
    scheduler = MeetingScheduler(
        meeting_repo=meeting_repo,
        conductor=conductor,
        participant_selector=selector,
        task_repo=task_repo,
        project_repo=proj_repo
    )

    incident_mtg = await scheduler.on_task_blocked(
        task_id=task1.task_id,
        blocker_reason="HMAC validation fails on Adyen payload due to missing timestamp header"
    )

    assert incident_mtg.meeting_type == MeetingType.INCIDENT_REVIEW
    assert "TASK-PAY-01" in incident_mtg.title
    assert "BossAI" in incident_mtg.participants
    assert backend.employee_id in incident_mtg.participants
    assert qa.employee_id in incident_mtg.participants

    # 4. Convene Meeting
    await conductor.convene_meeting(incident_mtg.meeting_id)

    # 5. Submit valid Fact-Grounded Report
    valid_report = ProgressReportCompiler.compile(
        employee_id=backend.employee_id,
        task_id=task1.task_id,
        what_was_done="Discovered signature mismatch during sandbox integration tests",
        what_remains="Patch webhook header parsing logic for timestamp tolerance",
        what_is_blocking="Adyen test sandbox sends non-standard header casing",
        execution_logs=["Signature verification failed for payload hash 0xdeadbeef", "exit code 1"],
        git_commits=["f4e3d2c: test(webhooks): add failing test for Adyen timestamp"],
        exit_code=1
    )
    collected = await conductor.collect_report(incident_mtg.meeting_id, valid_report)
    assert collected["factual_status"] == "blocked"

    # 6. Reject Fabricated Progress Percentage
    fake_report_dict = {
        "employee_id": frontend.employee_id,
        "task_id": task2.task_id,
        "what_was_done": "Working on React components",
        "what_remains": "Finishing up",
        "progress_percentage": 88,  # Fabricated!
        "verification_receipt": {"exit_code": 0}
    }
    with pytest.raises(FabricatedProgressError):
        await conductor.collect_report(incident_mtg.meeting_id, fake_report_dict)

    # 7. Record Strategic Decision with Rationale
    decision = await conductor.record_decision(
        meeting_id=incident_mtg.meeting_id,
        topic="Webhook Header Tolerance Policy",
        decision="Implement case-insensitive header normalization with +/- 300s timestamp drift",
        rationale="Eliminates Adyen vendor payload discrepancies without weakening security envelope",
        decided_by="BossAI",
        impact="Webhook Gateway middleware"
    )
    assert decision["decision_id"].startswith("DEC-")

    # 8. Allocate Action Item with Deadline
    due_date = utc_now() + timedelta(days=1)
    action_item = await conductor.add_action_item(
        meeting_id=incident_mtg.id,
        description="Patch and verify case-insensitive header normalizer in webhook gateway",
        assigned_to=backend.employee_id,
        deadline=due_date
    )
    assert action_item.status == ActionItemStatus.OPEN

    # 9. Conclude Meeting and verify minutes
    concluded_mtg = await conductor.conclude_meeting(incident_mtg.meeting_id)
    assert concluded_mtg.status == MeetingStatus.CONCLUDED
    assert concluded_mtg.minutes["total_reports"] == 1
    assert concluded_mtg.minutes["total_blockers"] == 1
    assert concluded_mtg.minutes["total_decisions"] == 1
    assert concluded_mtg.minutes["total_action_items"] == 1

    # 10. Next Standup: Automatic Carry-Over of Unresolved Action Items
    standup_mtg = await scheduler.trigger_daily_standup()
    await conductor.convene_meeting(standup_mtg.meeting_id)
    carried_items = await conductor.carry_over_unresolved(standup_mtg.id)

    assert len(carried_items) == 1
    assert "Patch and verify case-insensitive header normalizer" in carried_items[0].description
    assert carried_items[0].assigned_to == backend.employee_id
    assert carried_items[0].meeting_id == standup_mtg.id

    # Conclude Standup
    await conductor.conclude_meeting(standup_mtg.meeting_id)

    # 11. Compile Executive Briefing for Human Owner
    briefing_gen = ExecutiveBriefingGenerator(
        project_repo=proj_repo,
        task_repo=task_repo,
        employee_repo=emp_repo,
        meeting_repo=meeting_repo,
        perf_repo=perf_repo,
        approval_repo=app_repo,
        action_item_repo=action_repo
    )
    briefing = await briefing_gen.generate_briefing(project_id=project.id)

    assert briefing["milestones"][0]["project_name"] == "Project-PaymentEngine"
    assert briefing["milestones"][0]["blocked_tasks"] == 1
    assert briefing["active_workforce"]["total_employees"] == 3
    assert len(briefing["blockers"]) == 1
    assert briefing["blockers"][0]["task_id"] == "TASK-PAY-01"
    assert briefing["financial_costs"]["total_usd"] == 0.1875
    assert len(briefing["pending_approvals"]) == 1
    assert briefing["pending_approvals"][0]["action_type"] == "MERCHANT_API_KEY_ROTATION"
    assert briefing["action_items_summary"]["open_count"] >= 1


@pytest.mark.asyncio
async def test_phase3_fastapi_endpoints_integration(async_session: AsyncSession):
    """
    FastAPI Endpoints Verification for Phase 3:
    - /meetings/schedule
    - /meetings/
    - /meetings/{id}
    - /meetings/{id}/start
    - /meetings/{id}/reports
    - /meetings/{id}/decisions
    - /meetings/{id}/conclude
    - /meetings/action-items
    - /meetings/action-items/{id}
    - /meetings/briefing
    - /meetings/trigger/standup
    - /meetings/trigger/blocker
    """
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Schedule a Meeting
        res = await client.post(
            "/meetings/schedule",
            json={
                "title": "Security & Vulnerability Architecture Review",
                "meeting_type": "architecture_review",
                "agenda": ["Review Docker Seccomp Profile", "Inspect VNC Gateway"],
                "participants": ["BossAI", "ANC-EMP-SEC"]
            }
        )
        assert res.status_code == 200
        mtg_data = res.json()
        meeting_id = mtg_data["meeting_id"]
        assert mtg_data["status"] == "scheduled"

        # 2. Start Meeting
        res = await client.post(f"/meetings/{meeting_id}/start")
        assert res.status_code == 200
        started_data = res.json()
        assert started_data["status"] == "in_progress"

        # 3. Submit Fact-Grounded Progress Report
        res = await client.post(
            f"/meetings/{meeting_id}/reports",
            json={
                "employee_id": "ANC-EMP-SEC",
                "task_id": "TASK-SEC-01",
                "what_was_done": "Audited default seccomp profiles for container sandbox",
                "what_remains": "Block clone3 and bpf syscalls",
                "what_is_blocking": None,
                "verification_receipt": {
                    "exit_code": 0,
                    "git_commits": ["b1a2c3d"]
                }
            }
        )
        assert res.status_code == 200
        assert res.json()["status"] == "accepted"

        # 4. Anti-Fabrication: Reject fabricated percentage report
        bad_report_res = await client.post(
            f"/meetings/{meeting_id}/reports",
            json={
                "employee_id": "ANC-EMP-SEC",
                "task_id": "TASK-SEC-01",
                "what_was_done": "Made progress on seccomp",
                "what_remains": "Finishing",
                "progress_percentage": 92,  # Fabricated!
                "verification_receipt": {"exit_code": 0}
            }
        )
        assert bad_report_res.status_code == 400
        assert "Fabricated progress percentage" in bad_report_res.json()["detail"]

        # 5. Record Decision
        dec_res = await client.post(
            f"/meetings/{meeting_id}/decisions",
            json={
                "topic": "Seccomp Syscall Whitelist",
                "decision": "Enforce restrictive whitelist blocking bpf, ptrace, and unshare",
                "rationale": "Prevents container breakout attempts without impacting standard dev tools",
                "decided_by": "BossAI"
            }
        )
        assert dec_res.status_code == 200
        assert dec_res.json()["status"] == "recorded"

        # 6. Create Action Item
        action_res = await client.post(
            "/meetings/action-items",
            json={
                "meeting_id": meeting_id,
                "description": "Deploy hardened seccomp JSON to workstation profiles",
                "assigned_to": "ANC-EMP-SEC"
            }
        )
        assert action_res.status_code == 200
        action_data = action_res.json()
        action_item_id = action_data["id"]
        assert action_data["status"] == "open"

        # 7. Update Action Item Status
        patch_res = await client.patch(
            f"/meetings/action-items/{action_item_id}",
            json={"status": "in_progress"}
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["status"] == "in_progress"

        # 8. List Action Items
        list_items_res = await client.get("/meetings/action-items")
        assert list_items_res.status_code == 200
        assert len(list_items_res.json()) >= 1

        # 9. Conclude Meeting
        conclude_res = await client.post(f"/meetings/{meeting_id}/conclude")
        assert conclude_res.status_code == 200
        concluded_data = conclude_res.json()
        assert concluded_data["status"] == "concluded"
        assert len(concluded_data["decisions"]) >= 1
        assert len(concluded_data["action_items"]) >= 1

        # 9b. Protection: Cannot restart already concluded meeting
        restart_res = await client.post(f"/meetings/{meeting_id}/start")
        assert restart_res.status_code in (400, 404)
        assert "already concluded" in restart_res.json()["detail"]

        # 10. Get Meeting Details
        detail_res = await client.get(f"/meetings/{meeting_id}")
        assert detail_res.status_code == 200
        detail_data = detail_res.json()
        assert detail_data["meeting_id"] == meeting_id
        assert detail_data["minutes"]["total_reports"] >= 1

        # 11. Trigger Daily Standup
        standup_res = await client.post("/meetings/trigger/standup")
        assert standup_res.status_code == 200
        standup_data = standup_res.json()
        assert standup_data["meeting_type"] == "daily_standup"
        new_standup_id = standup_data["meeting_id"]

        # 11b. Conduct Standup end-to-end with ISO deadline string action item
        conduct_res = await client.post(
            f"/meetings/{new_standup_id}/conduct",
            json={
                "action_items": [
                    {
                        "description": "Verify container AppArmor profiles",
                        "assigned_to": "ANC-EMP-SEC",
                        "deadline": "2026-12-01T12:00:00Z"
                    }
                ],
                "decisions": [
                    {
                        "topic": "Container Profiles",
                        "decision": "Lockdown AppArmor",
                        "rationale": "Security baseline"
                    }
                ]
            }
        )
        assert conduct_res.status_code == 200
        conduct_data = conduct_res.json()
        assert conduct_data["status"] == "concluded"
        assert len(conduct_data["action_items"]) == 2
        assert any(ai["description"] == "Verify container AppArmor profiles" for ai in conduct_data["action_items"])
        assert any("[Carried Over] Deploy hardened seccomp JSON" in ai["description"] for ai in conduct_data["action_items"])

        # 12. Trigger Blocker Meeting
        blocker_res = await client.post(
            "/meetings/trigger/blocker",
            json={"task_id": "TASK-SEC-01", "blocker_reason": "Kernel module missing"}
        )
        assert blocker_res.status_code == 200
        assert blocker_res.json()["meeting_type"] == "incident_review"

        # 13. Get Executive Briefing
        briefing_res = await client.get("/meetings/briefing")
        assert briefing_res.status_code == 200
        briefing_data = briefing_res.json()
        assert "generated_at" in briefing_data
        assert "executive_summary" in briefing_data
        assert "milestones" in briefing_data
        assert "financial_costs" in briefing_data
