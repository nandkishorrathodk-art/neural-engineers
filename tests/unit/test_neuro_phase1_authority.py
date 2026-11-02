from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session, require_owner_identity
from neural.api.main import create_app
from neural.boss.brain import BossBrain
from neural.constants import TaskStatus
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
