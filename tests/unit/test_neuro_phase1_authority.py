from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.boss.brain import BossBrain
from neural.constants import EmployeeStatus, ExecutionMode, TaskStatus
from neural.db.models import TaskAttempt
from neural.db.repositories import (
    AuditRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory


@pytest.mark.asyncio
async def test_production_owner_identity_rejects_worker_credentials(monkeypatch):
    from neural.config import get_settings

    owner_token = "owner-token-" + "o" * 40
    worker_token = "worker-token-" + "w" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("VERIFIER_API_TOKEN", "verifier-token-" + "v" * 40)
    monkeypatch.setenv("APPROVER_API_TOKEN", "approver-token-" + "a" * 40)
    get_settings.cache_clear()

    assert await require_owner_identity(f"Bearer {owner_token}") == "human-owner"
    with pytest.raises(HTTPException) as rejected:
        await require_owner_identity(f"Bearer {worker_token}")
    assert rejected.value.status_code == 401


@pytest.mark.asyncio
async def test_boss_staffing_requires_verified_skills(async_session):
    employee_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(employee_repo)
    employee = await factory.hire_employee(
        name="Evidence Test Engineer",
        title="Backend Engineer",
        initial_skills=["Python"],
    )
    plan = {
        "phases": [
            {
                "tasks": [
                    {
                        "role_required": "backend-engineer",
                        "skills_required": ["Python"],
                    }
                ]
            }
        ]
    }

    assert BossBrain._get_verified_skill_gaps(plan, [employee]) == {
        "backend-engineer": ["Python"]
    }

    employee.skills[0].verified = True
    await async_session.commit()
    assert BossBrain._get_verified_skill_gaps(plan, [employee]) == {}


@pytest.mark.asyncio
async def test_production_objective_is_owner_authenticated_and_audited(
    async_session,
    monkeypatch,
):
    from neural.config import get_settings

    owner_token = "owner-token-" + "o" * 40
    worker_token = "worker-token-" + "w" * 40
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", owner_token)
    monkeypatch.setenv("WORKER_API_TOKEN", worker_token)
    monkeypatch.setenv("VERIFIER_API_TOKEN", "verifier-token-" + "v" * 40)
    monkeypatch.setenv("APPROVER_API_TOKEN", "approver-token-" + "a" * 40)
    get_settings.cache_clear()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = {"objective": "Create an evidence-backed API service"}
        assert (await client.post("/boss/objective", json=payload)).status_code == 401
        assert (
            await client.post(
                "/boss/objective",
                json=payload,
                headers={"Authorization": f"Bearer {worker_token}"},
            )
        ).status_code == 401

        accepted = await client.post(
            "/boss/objective",
            json=payload,
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert accepted.status_code == 200
        project_id = accepted.json()["id"]

        assert (await client.get(f"/boss/projects/{project_id}/report")).status_code == 401
        report_response = await client.get(
            f"/boss/projects/{project_id}/report",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert report_response.status_code == 200
        report = report_response.json()
        assert report["task_counts"]["verified"] == 0
        assert report["task_counts"]["completed_without_verification"] == 0
        assert report["task_counts"]["assigned"] >= 1

    audit_records = await AuditRepository(async_session).list_all()
    owner_events = [record for record in audit_records if record.action == "SUBMIT_OBJECTIVE"]
    staffing_events = [record for record in audit_records if record.action == "STAFFING_DECISION"]
    assert owner_events and owner_events[0].actor == "human-owner"
    assert staffing_events and staffing_events[0].actor == "BossAI"
    assert staffing_events[0].details["recruited_employee_ids"]


@pytest.mark.asyncio
async def test_project_report_distinguishes_unverified_completion_and_attempt_failure(async_session):
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    project = await project_repo.create(name="Report evidence", objective="Report stored task state")
    await task_repo.create(
        task_id="TASK-UNVERIFIED-REPORT",
        project_id=project.id,
        title="Legacy completion",
        objective="Detect a completion missing proof",
        scope="Reporting",
        acceptance_criteria="A passing verifier receipt exists",
        status=TaskStatus.COMPLETED,
        verification_result=None,
    )
    failed_task = await task_repo.create(
        task_id="TASK-FAILED-REPORT",
        project_id=project.id,
        title="Failed execution",
        objective="Expose an actual failed attempt",
        scope="Reporting",
        acceptance_criteria="Failure details are visible",
        status=TaskStatus.FAILED,
    )
    passing_task = await task_repo.create(
        task_id="TASK-VERIFIED-REPORT",
        project_id=project.id,
        title="Verified work",
        objective="Include a passing independent receipt",
        scope="Reporting",
        acceptance_criteria="A passing verifier receipt is stored",
    )
    await task_repo.complete_with_receipt(
        passing_task.task_id,
        {
            "passed": True,
            "notes": "Acceptance checks passed",
            "evidence_refs": ["sha256:verified-artifact"],
            "artifacts": ["verified-artifact"],
            "test_summary": {"passed": 2},
        },
    )
    async_session.add(
        TaskAttempt(
            task_id=failed_task.id,
            attempt_number=1,
            worker_id="worker-test",
            fencing_token=1,
            status="failed",
            lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None),
            error_message="Process returned exit code 2",
            execution_receipt={},
        )
    )
    await async_session.commit()

    report = await BossBrain(project_repo=project_repo, task_repo=task_repo).build_project_report(project.id)
    assert report is not None
    assert report["task_counts"]["verified"] == 1
    assert report["task_counts"]["completed_without_verification"] == 1
    assert report["task_counts"]["failed"] == 1
    assert report["task_counts"]["failed_attempts"] == 1
    reported_failure = next(task for task in report["tasks"] if task["task_id"] == "TASK-FAILED-REPORT")
    assert reported_failure["attempts"][0]["error_message"] == "Process returned exit code 2"
    reported_success = next(task for task in report["tasks"] if task["task_id"] == "TASK-VERIFIED-REPORT")
    assert reported_success["verified"] is True
    assert reported_success["evidence_refs"] == ["sha256:verified-artifact"]


@pytest.mark.asyncio
async def test_worker_failure_retries_only_retryable_errors_within_budget(async_session):
    project = await ProjectRepository(async_session).create(name="Recovery", objective="Retry safely")
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-RECOVERY-RETRY",
        project_id=project.id,
        title="Retry transient failure",
        objective="Retry connection timeout",
        scope="Execution",
        acceptance_criteria="Independent verification passes",
        status=TaskStatus.RUNNING,
        max_retries=1,
        fencing_token=1,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-test",
        fencing_token=1,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2),
        execution_receipt={},
    )
    async_session.add(attempt)
    await async_session.commit()

    retry = await task_repo.fail_task_attempt(task.task_id, "worker-test", 1, "connection timeout")
    assert retry == {"retrying": True, "retry_count": 1, "retry_after_seconds": 1}
    retried_task = await task_repo.get_by_task_id(task.task_id)
    assert retried_task.status == TaskStatus.ASSIGNED
    assert retried_task.fencing_token == 2
    first_attempt = await async_session.get(TaskAttempt, attempt.id)
    assert first_attempt.lease_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)

    next_attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=2,
        worker_id="worker-test",
        fencing_token=3,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2),
        execution_receipt={},
    )
    retried_task.status = TaskStatus.RUNNING
    retried_task.fencing_token = 3
    async_session.add(next_attempt)
    await async_session.commit()

    exhausted = await task_repo.fail_task_attempt(task.task_id, "worker-test", 3, "connection timeout")
    assert exhausted == {"retrying": False, "retry_count": 1, "retry_after_seconds": 0}
    final_task = await task_repo.get_by_task_id(task.task_id)
    assert final_task.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_expired_worker_lease_consumes_retry_budget(async_session):
    project = await ProjectRepository(async_session).create(name="Lease recovery", objective="Recover worker loss")
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-LEASE-TEST",
        name="Lease Worker",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.WORKING,
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-EXPIRED-LEASE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Recover expired worker",
        objective="Bound lease retries",
        scope="Execution",
        acceptance_criteria="Retry budget is respected",
        status=TaskStatus.RUNNING,
        fencing_token=1,
        max_retries=1,
    )
    expired_attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-lost",
        fencing_token=1,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
        execution_receipt={},
    )
    async_session.add(expired_attempt)
    await async_session.commit()

    assert await task_repo.claim_next_task("worker-replacement") is None
    recovered = await task_repo.get_by_task_id(task.task_id)
    assert recovered.status == TaskStatus.ASSIGNED
    assert recovered.retry_count == 1
    assert recovered.fencing_token == 2
    report = await BossBrain(project_repo=ProjectRepository(async_session), task_repo=task_repo).build_project_report(project.id)
    assert report is not None
    assert report["task_counts"]["retrying"] == 1
    retried_report = report["tasks"][0]
    assert retried_report["retry_count"] == 1
    assert retried_report["max_retries"] == 1
    await async_session.refresh(expired_attempt)
    assert expired_attempt.status == "failed"
    assert expired_attempt.lease_expires_at > datetime.now(timezone.utc).replace(tzinfo=None)
    assert await task_repo.claim_next_task("worker-replacement") is None


@pytest.mark.asyncio
async def test_expired_worker_lease_exhaustion_fails_task_and_releases_employee(async_session):
    project = await ProjectRepository(async_session).create(name="Lease exhaustion", objective="Stop retry loops")
    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-LEASE-EXHAUST",
        name="Exhausted Worker",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.WORKING,
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-EXPIRED-LEASE-EXHAUST",
        project_id=project.id,
        assigned_to=employee.id,
        title="Exhaust worker retries",
        objective="Stop retrying after the budget",
        scope="Execution",
        acceptance_criteria="Task fails and worker is released",
        status=TaskStatus.RUNNING,
        fencing_token=4,
        retry_count=1,
        max_retries=1,
    )
    expired_attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=2,
        worker_id="worker-lost",
        fencing_token=4,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1),
        execution_receipt={},
    )
    async_session.add(expired_attempt)
    await async_session.commit()

    assert await task_repo.claim_next_task("worker-replacement") is None
    failed_task = await task_repo.get_by_task_id(task.task_id)
    released_employee = await employee_repo.get_by_employee_id(employee.employee_id)
    assert failed_task.status == TaskStatus.FAILED
    assert failed_task.retry_count == 1
    assert released_employee.status == EmployeeStatus.IDLE


@pytest.mark.asyncio
async def test_cancelling_task_fences_and_closes_active_worker_attempt(async_session):
    project = await ProjectRepository(async_session).create(name="Cancellation", objective="Stop active work")
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-CANCEL-ACTIVE-WORKER",
        project_id=project.id,
        title="Cancel leased work",
        objective="Stop the current worker attempt",
        scope="Execution",
        acceptance_criteria="Attempt is terminal and stale heartbeat is rejected",
        status=TaskStatus.RUNNING,
        fencing_token=7,
    )
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-active",
        fencing_token=7,
        status="running",
        lease_expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2),
        execution_receipt={},
    )
    async_session.add(attempt)
    await async_session.commit()

    await task_repo.update_status(task.task_id, TaskStatus.CANCELLED)
    cancelled_task = await task_repo.get_by_task_id(task.task_id)
    await async_session.refresh(attempt)
    assert cancelled_task.status == TaskStatus.CANCELLED
    assert cancelled_task.fencing_token == 8
    assert attempt.status == "cancelled"
    assert attempt.finished_at is not None
    assert await task_repo.heartbeat_task_attempt(task.task_id, "worker-active", 7) is None


@pytest.mark.asyncio
async def test_worker_claim_filters_execution_modes_before_leasing(async_session):
    project = await ProjectRepository(async_session).create(name="Mode filtering", objective="Only claim supported work")
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-MODE-TEST",
        name="Mode Worker",
        title="Backend Engineer",
        system_prompt="test employee",
        status=EmployeeStatus.WORKING,
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-TERMINAL-MODE",
        project_id=project.id,
        assigned_to=employee.id,
        title="Terminal task",
        objective="Run terminal work",
        scope="Execution",
        acceptance_criteria="A supported-mode worker may claim this",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
    )

    assert await task_repo.claim_next_task("worker-gui", execution_modes=[ExecutionMode.GUI]) is None
    assert await task_repo.list_attempts_by_project(project.id) == []
    persisted = await task_repo.get_by_task_id(task.task_id)
    assert persisted.fencing_token == 0
