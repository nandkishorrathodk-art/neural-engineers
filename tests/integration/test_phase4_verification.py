import pytest
from neural.boss.delegator import TaskDelegator
from neural.boss.recovery import FailureRecoveryManager
from neural.constants import EmployeeStatus, TaskStatus, VerificationStatus
from neural.db.repositories import (
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.evaluation.verifier import IndependentVerifier
from neural.exceptions import UnverifiedTaskError
from neural.tasks.artifacts import ArtifactManager
from neural.tasks.scheduler import TaskScheduler
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_phase4_empirical_verification_and_skill_certification(
    async_session: AsyncSession,
    tmp_path
):
    """
    Phase 4 Master Done-Gate Integration Test:
    1. Workforce Bootstrap: Hire employees with unverified initial skills.
    2. Boss AI plans project with DAG tasks requiring specific technical capabilities.
    3. Artifact Management: Store deliverables with cryptographic SHA-256 checksums and version history.
    4. Strict Gate Enforcement: Reject task transition to 'completed' without independent QA receipt.
    5. Independent QA Verifier: Run automated tests, AST syntax parsing, security scanning, acceptance criteria.
    6. Failure Recovery Loop: Classify failure, compute exponential backoff, return diagnostic feedback.
    7. Artifact Versioning: Store revised artifact as version 2; verification passes with signed receipt.
    8. Empirical Skill Certification: Skills transition verified=False -> verified=True with task evidence.
    9. Delegator / Scheduler Prioritization: Boss AI matches subsequent task to certified employee over unverified employee.
    """
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session, strict_verification=True)
    employee_repo = EmployeeRepository(async_session)

    storage_base = str(tmp_path / "artifacts")
    artifact_manager = ArtifactManager(session=async_session, storage_base_path=storage_base)
    delegator = TaskDelegator()
    recovery_manager = FailureRecoveryManager(initial_delay=0.01, backoff_multiplier=2.0)
    verifier = IndependentVerifier(employee_repo=employee_repo, task_repo=task_repo)

    # 1. Recruit 2 developers with declared unverified skills
    factory = EmployeeFactory(employee_repo)
    emp_alex = await factory.hire_employee(
        name="Alex River",
        title="Senior Backend Engineer",
        initial_skills=["Python", "FastAPI"]
    )
    emp_jordan = await factory.hire_employee(
        name="Jordan Blake",
        title="Junior Backend Developer",
        initial_skills=["Python", "FastAPI"]
    )

    # Both start with unverified skills
    assert all(not s.verified for s in emp_alex.skills)
    assert all(not s.verified for s in emp_jordan.skills)

    # 2. Boss AI initializes project
    project = await project_repo.create(
        name="Project-PaymentGateway-V2",
        objective="Deliver secure high-throughput payment webhook processor"
    )

    task1 = await task_repo.create(
        task_id="TASK-PHASE4-01",
        project_id=project.id,
        title="Webhook Processor Implementation",
        objective="Implement Stripe webhook processor with HMAC validation",
        scope="Backend Services",
        acceptance_criteria="Green unit tests and clean code quality",
        required_capabilities=["Python", "FastAPI"],
        assigned_to=emp_alex.id,
        status=TaskStatus.RUNNING,
        retry_count=0,
        max_retries=3
    )

    # 3. Strict Gate: Verify task CANNOT complete without independent verification receipt
    with pytest.raises(UnverifiedTaskError):
        await task_repo.update_status(task1.task_id, TaskStatus.COMPLETED)

    # 4. Attempt 1: Worker submits buggy code (Syntax Error)
    buggy_code = "def process_webhook(payload):\n    return payload['event'\n"  # missing closing bracket
    art_v1 = await artifact_manager.store_artifact(
        project_id=project.id,
        task_id=task1.task_id,
        name="webhook.py",
        content=buggy_code,
        artifact_type="code"
    )
    assert art_v1.version == 1
    assert artifact_manager.verify_checksum(art_v1) is True

    # Independent QA evaluates Attempt 1
    qa_run_1 = await verifier.verify_task_result(
        task_id=task1.task_id,
        employee_id=emp_alex.employee_id,
        artifacts=[art_v1.path],
        test_exit_code=1,
        tested_skills=["Python", "FastAPI"],
        acceptance_criteria=task1.acceptance_criteria,
        file_contents={art_v1.path: buggy_code}
    )

    assert qa_run_1["passed"] is False
    assert qa_run_1["code_quality"]["syntax_valid"] is False
    assert "syntax error" in qa_run_1["notes"].lower() or "syntax_error" in str(qa_run_1["code_quality"]["issues"])

    # 5. Failure Recovery: classify failure and compute backoff
    rec_action_1 = recovery_manager.handle_failure(
        task=task1,
        error=qa_run_1["notes"],
        exit_code=1
    )
    assert rec_action_1.action_type == "retry"
    assert rec_action_1.retry_count == 1
    assert rec_action_1.backoff_delay_seconds > 0.0
    assert task1.retry_count == 1
    assert "syntax" in rec_action_1.diagnostic_feedback.lower()

    # Skills MUST still be unverified after failed QA run
    alex_reloaded = await employee_repo.get_by_employee_id(emp_alex.employee_id)
    assert all(not s.verified for s in alex_reloaded.skills)

    # 6. Attempt 2: Worker fixes code based on diagnostic feedback
    fixed_code = (
        "def process_webhook(payload: dict) -> dict:\n"
        "    event = payload.get('event')\n"
        "    return {'status': 'processed', 'event': event}\n"
    )
    art_v2 = await artifact_manager.store_artifact(
        project_id=project.id,
        task_id=task1.task_id,
        name="webhook.py",
        content=fixed_code,
        artifact_type="code"
    )
    assert art_v2.version == 2
    assert art_v2.checksum != art_v1.checksum

    # Version history holds both versions
    history = await artifact_manager.get_version_history(project.id, "webhook.py")
    assert len(history) == 2
    assert [a.version for a in history] == [1, 2]

    # Independent QA evaluates Attempt 2
    qa_run_2 = await verifier.verify_task_result(
        task_id=task1.task_id,
        employee_id=emp_alex.employee_id,
        artifacts=[art_v2.path],
        test_exit_code=0,
        tested_skills=["Python", "FastAPI"],
        acceptance_criteria=task1.acceptance_criteria,
        file_contents={art_v2.path: fixed_code}
    )

    assert qa_run_2["passed"] is True
    assert qa_run_2["verification_status"] == VerificationStatus.PASSED.value
    assert qa_run_2["code_quality"]["syntax_valid"] is True
    assert len(qa_run_2["receipt"]["signature"]) == 64

    # 7. Strict Gate Acceptance: Complete task with passing verification receipt
    completed_task = await verifier.gate_task_completion(task1.task_id, qa_run_2["receipt"])
    assert completed_task.status == TaskStatus.COMPLETED
    assert completed_task.completed_at is not None
    assert completed_task.verification_result["passed"] is True

    # 8. Empirical Skill Certification: Alex's skills transition to verified=True!
    alex_certified = await employee_repo.get_by_employee_id(emp_alex.employee_id)
    alex_verified_skills = [s for s in alex_certified.skills if s.verified]
    assert len(alex_verified_skills) == 2
    assert {"Python", "FastAPI"}.issubset({s.skill_name for s in alex_verified_skills})
    assert task1.task_id in alex_verified_skills[0].evidence_task_ids

    # Jordan remains unverified
    jordan_unverified = await employee_repo.get_by_employee_id(emp_jordan.employee_id)
    assert all(not s.verified for s in jordan_unverified.skills)

    # 9. Delegator Prioritization: Boss AI assigns next task to certified Alex over unverified Jordan!
    task2 = await task_repo.create(
        task_id="TASK-PHASE4-02",
        project_id=project.id,
        title="Payment Router Microservice",
        objective="Route incoming transactions",
        scope="Backend Services",
        acceptance_criteria="Clean receipts",
        required_capabilities=["Python", "FastAPI"],
        status=TaskStatus.PENDING
    )

    # Ensure both are idle
    await employee_repo.update_status(emp_alex.employee_id, EmployeeStatus.IDLE)
    await employee_repo.update_status(emp_jordan.employee_id, EmployeeStatus.IDLE)

    available_candidates = await employee_repo.list_all()

    # TaskDelegator matches task2: Alex has 2 VERIFIED skills, Jordan has 0 verified skills
    selected_assignee = delegator.select_best_assignee(task2, available_candidates)
    assert selected_assignee is not None
    assert selected_assignee.employee_id == emp_alex.employee_id, (
        f"Expected certified employee {emp_alex.employee_id} to be prioritized, but got {selected_assignee.employee_id}"
    )

    # Verify score calculation
    score_alex = delegator.score_employee_for_task(selected_assignee, ["Python", "FastAPI"])
    score_jordan = delegator.score_employee_for_task(jordan_unverified, ["Python", "FastAPI"])
    assert score_alex[0] == 2  # 2 verified matches
    assert score_jordan[0] == 0  # 0 verified matches
    assert score_alex > score_jordan


@pytest.mark.asyncio
async def test_scheduler_dag_execution_with_verifier_and_recovery(
    async_session: AsyncSession,
    tmp_path
):
    """
    Verifies that TaskScheduler seamlessly executes a DAG using IndependentVerifier
    and FailureRecoveryManager, enforcing strict gating and auto-retrying failures.
    """
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)

    factory = EmployeeFactory(emp_repo)
    await factory.hire_employee(name="Chris Evans", title="Developer", initial_skills=["Python"])

    project = await proj_repo.create(name="Project-Scheduler-Verified", objective="Verified DAG")

    t1 = await task_repo.create(
        task_id="TASK-SCHED-01",
        project_id=project.id,
        title="Verified Task 1",
        objective="Run and verify",
        scope="Core",
        acceptance_criteria="Pass tests",
        required_capabilities=["Python"],
        status=TaskStatus.PENDING
    )

    verifier = IndependentVerifier(employee_repo=emp_repo, task_repo=task_repo)
    recovery = FailureRecoveryManager(max_retries=2, initial_delay=0.01)

    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=emp_repo,
        verifier=verifier,
        recovery_manager=recovery,
        enforce_verification=True
    )

    attempts = {"count": 0}

    async def flaky_worker(task):
        attempts["count"] += 1
        if attempts["count"] == 1:
            # First attempt fails tests
            return {
                "status": "success",  # worker executed, but QA tests will fail
                "test_exit_code": 1,
                "artifacts": ["/workspace/attempt1.py"]
            }
        else:
            # Second attempt passes
            return {
                "status": "success",
                "test_exit_code": 0,
                "artifacts": ["/workspace/attempt2.py"]
            }

    result = await scheduler.execute_dag(project.id, worker_func=flaky_worker, poll_interval=0.01)

    assert result["success"] is True
    assert "TASK-SCHED-01" in result["completed"]
    assert attempts["count"] == 2

    # Task was verified and marked completed in database
    db_task = await task_repo.get_by_task_id(t1.task_id)
    assert db_task.status == TaskStatus.COMPLETED
    assert db_task.verification_result["passed"] is True
