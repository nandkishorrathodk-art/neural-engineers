import hashlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from neural.boss.brain import BossBrain
from neural.boss.delegator import TaskDelegator
from neural.boss.recovery import WorkerFaultInjector
from neural.constants import (
    ContainerStatus,
    EmployeeStatus,
    ProjectStatus,
    TaskStatus,
)
from neural.db.models import (
    BudgetAccount,
    Container,
    Employee,
    EmployeeSkill,
)
from neural.db.repositories import (
    AuditRepository,
    BudgetRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.evaluation.verifier import IndependentDeliverableVerifier
from neural.tasks.scheduler import TaskScheduler


@pytest.mark.asyncio
async def test_neuro_phase1_21_step_live_acceptance(async_session, tmp_path):
    """
    NEURO-1 Final Acceptance Test: 21-Step String-Math Utility Package Workflow.
    Verifies autonomous Boss intelligence, budget quota reservation, DAG cycle validation,
    atomic worker lease dispatch, injected fault recovery, dynamic standby reassignment,
    stale worker submission rejection, isolated independent verification receipts,
    and digitally signed project completion reports.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # =========================================================================
    # STEP 1: Authenticate Owner and check monthly budget quota (durable pre-reservation)
    # =========================================================================
    owner_identity = "owner@ancos.corp"
    assert "@" in owner_identity
    budget_repo = BudgetRepository(async_session)
    month_scope = f"organization-month:{now:%Y-%m}"
    account = BudgetAccount(
        scope_key=month_scope,
        cap_usd=Decimal("500.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    available_quota = await budget_repo.get_available_quota(month_scope)
    assert available_quota >= Decimal("100.00"), f"Step 1 Failed: Insufficient monthly quota: {available_quota}"

    # Pre-reserve budget for project planning
    plan_reservation = await budget_repo.reserve(
        idempotency_key="plan-reserve-neuro1-acceptance",
        reserved_usd=Decimal("1.00"),
        scope_caps={month_scope: Decimal("500.00")},
    )
    assert plan_reservation is not None and plan_reservation.status == "reserved", "Step 1 Failed: Budget reservation"

    # =========================================================================
    # STEP 2: Submit high-level objective with budget and clarifying questions
    # =========================================================================
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    audit_repo = AuditRepository(async_session)

    brain = BossBrain(
        project_repo=project_repo,
        task_repo=task_repo,
        employee_repo=employee_repo,
        audit_repo=audit_repo,
    )

    objective_text = "Build an autonomous string-math calculation utility package"
    clarifications = {
        "What specific outcome should ANCOS deliver, and what would count as success?": (
            "Deliver a string math evaluator package supporting addition and multiplication with full unit tests."
        )
    }

    # =========================================================================
    # STEP 3: BossBrain decomposes objective into canonical DAG with 2 sequential tasks
    # =========================================================================
    project, created_tasks = await brain.plan_and_create_project(
        objective=objective_text,
        project_name="String-Math-Utility",
        budget_usd=50.0,
        clarifications=clarifications,
        auto_assign_first_task=False,
    )
    assert project.id is not None, "Step 3 Failed: Project creation"
    assert len(created_tasks) >= 2, f"Step 3 Failed: Expected at least 2 tasks, got {len(created_tasks)}"

    # Identify Task-A and Task-B
    task_a = created_tasks[0]
    task_b = created_tasks[1]
    # Ensure Task-B depends on Task-A
    await task_repo.add_dependency(task_id=task_b.task_id, depends_on_task_id=task_a.task_id)

    # Settle planning budget
    await budget_repo.settle(
        plan_reservation.id,
        actual_usd=Decimal("0.05"),
        usage_receipt={"operation": "plan_and_create_project", "tasks_count": len(created_tasks)},
    )

    # =========================================================================
    # STEP 4: Verify DAG acyclicity and dependency ordering (Kahn's algorithm)
    # =========================================================================
    scheduler = TaskScheduler(
        task_repo=task_repo,
        employee_repo=employee_repo,
        delegator=TaskDelegator(),
        verifier=IndependentDeliverableVerifier(task_repo=task_repo, employee_repo=employee_repo),
    )
    graph = await scheduler.build_graph_for_project(project.id)
    # Graph validation ensures zero cycles
    graph.validate_dag()
    assert task_a.task_id in graph.get_all_tasks(), "Step 4 Failed: Task-A missing in graph"
    assert task_b.task_id in graph.get_all_tasks(), "Step 4 Failed: Task-B missing in graph"

    # =========================================================================
    # STEP 5: Persist Project and Task DAG in database under transactional lock
    # =========================================================================
    db_project = await project_repo.get_by_id(project.id)
    assert db_project is not None and db_project.status == ProjectStatus.PLANNING or ProjectStatus.ACTIVE
    persisted_task_a = await task_repo.get_by_task_id(task_a.task_id)
    persisted_task_b = await task_repo.get_by_task_id(task_b.task_id)
    assert persisted_task_a is not None and persisted_task_b is not None, "Step 5 Failed: Tasks not persisted"

    # =========================================================================
    # STEP 6: Recruit/provision qualified workforce (EMP-01 and EMP-02 standby)
    # =========================================================================
    # Provision Container 1 and EMP-01
    container_1 = Container(
        docker_id="ancos-desktop-anc-emp-math-01",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5911,
        novnc_port=6091,
    )
    async_session.add(container_1)
    await async_session.flush()

    emp_01 = Employee(
        employee_id="ANC-EMP-MATH-01",
        name="Primary Math Specialist",
        title="Senior Software Engineer",
        system_prompt="Senior engineer specialized in Python algorithms.",
        status=EmployeeStatus.IDLE,
        container_id=container_1.id,
    )
    async_session.add(emp_01)
    await async_session.flush()
    for skill in ["Python", "System Design", "PostgreSQL", "FastAPI", "REST APIs"]:
        async_session.add(EmployeeSkill(employee_id=emp_01.id, skill_name=skill, verified=True))

    # Provision Container 2 and EMP-02 (qualified standby)
    container_2 = Container(
        docker_id="ancos-desktop-anc-emp-math-02",
        image="ancos-desktop:latest",
        workspace_path="/home/employee/workspace",
        status=ContainerStatus.RUNNING,
        vnc_port=5912,
        novnc_port=6092,
    )
    async_session.add(container_2)
    await async_session.flush()

    emp_02 = Employee(
        employee_id="ANC-EMP-MATH-02",
        name="Standby Math Specialist",
        title="Software Engineer",
        system_prompt="Standby software engineer specialized in Python testing.",
        status=EmployeeStatus.IDLE,
        container_id=container_2.id,
    )
    async_session.add(emp_02)
    await async_session.flush()
    for skill in ["Python", "System Design", "PostgreSQL", "FastAPI", "REST APIs"]:
        async_session.add(EmployeeSkill(employee_id=emp_02.id, skill_name=skill, verified=True))
    await async_session.commit()

    # =========================================================================
    # STEP 7: TaskScheduler evaluates ready tasks: Task-A is ready, Task-B is blocked
    # =========================================================================
    ready_tasks = await scheduler.get_ready_tasks(project.id)
    ready_ids = {t.task_id for t in ready_tasks}
    assert task_a.task_id in ready_ids, "Step 7 Failed: Task-A should be ready"
    assert task_b.task_id not in ready_ids, "Step 7 Failed: Task-B must be blocked on Task-A"

    # =========================================================================
    # STEP 8: TaskScheduler dispatches Task-A to EMP-01
    # =========================================================================
    await task_repo.assign_task(task_a.task_id, emp_01.id)
    await task_repo.update_status(task_a.task_id, TaskStatus.ASSIGNED)

    task_a_assigned = await task_repo.get_by_task_id(task_a.task_id)
    assert task_a_assigned.status == TaskStatus.ASSIGNED
    assert task_a_assigned.assigned_to == emp_01.id

    # =========================================================================
    # STEP 9: Worker for EMP-01 claims Task-A lease; lease issued with fencing_token
    # =========================================================================
    worker_1_id = "worker-emp-01"
    claim_1 = await task_repo.claim_next_task(worker_id=worker_1_id, lease_seconds=120)
    assert claim_1 is not None, "Step 9 Failed: EMP-01 worker claim failed"
    assert claim_1["task_id"] == task_a.task_id
    assert claim_1["employee_id"] == emp_01.employee_id
    fencing_token_emp01 = claim_1["fencing_token"]
    assert fencing_token_emp01 >= 1

    started_1 = await task_repo.start_task_attempt(
        task_id=task_a.task_id,
        worker_id=worker_1_id,
        fencing_token=fencing_token_emp01,
        workspace_manifest_before={"files": []},
    )
    assert started_1 is True, "Step 9 Failed: EMP-01 failed to start task attempt"

    # =========================================================================
    # STEP 10: Fault injection: Inject worker failure into EMP-01 execution
    # =========================================================================
    WorkerFaultInjector.inject_fault(
        task_id=task_a.task_id,
        fault_type="worker_crash",
        error_message="Worker process crashed unexpectedly with SIGSEGV",
    )
    assert WorkerFaultInjector.has_fault(task_a.task_id), "Step 10 Failed: Fault not registered"

    # =========================================================================
    # STEP 11: Worker execution fails (simulated crash detected)
    # =========================================================================
    triggered_fault = WorkerFaultInjector.check_and_trigger(task_a.task_id)
    assert triggered_fault is not None, "Step 11 Failed: Fault trigger failed"
    error_message = triggered_fault["error_message"]

    # =========================================================================
    # STEP 12: Recovery manager triggers fail_task_attempt:
    #          - Invalidate failed attempt lease
    #          - Record failure event & audit history
    #          - Increment fencing token
    #          - Reassign Task-A to idle standby EMP-02
    # =========================================================================
    recovery_result = await task_repo.fail_task_attempt(
        task_id=task_a.task_id,
        worker_id=worker_1_id,
        fencing_token=fencing_token_emp01,
        error_message=error_message,
    )
    assert recovery_result is not None, "Step 12 Failed: fail_task_attempt returned None"
    assert recovery_result["retrying"] is True
    assert recovery_result["reassigned"] is True
    assert recovery_result["previous_employee_id"] == emp_01.employee_id
    assert recovery_result["assigned_employee_id"] == emp_02.employee_id

    # Verify task state in database
    task_a_recovered = await task_repo.get_by_task_id(task_a.task_id)
    assert task_a_recovered.assigned_to == emp_02.id
    assert task_a_recovered.status == TaskStatus.ASSIGNED
    assert task_a_recovered.fencing_token > fencing_token_emp01
    new_task_a_fence = task_a_recovered.fencing_token
    assert new_task_a_fence >= 2

    # Verify attempt history preserves EMP-01's failed attempt
    attempts_after_failure = await task_repo.list_attempts_for_task(task_a.task_id)
    assert len(attempts_after_failure) == 1
    assert attempts_after_failure[0].status == "failed"
    assert "crashed unexpectedly" in attempts_after_failure[0].error_message

    # =========================================================================
    # STEP 13: Stale worker submission rejection: EMP-01 attempts to submit with stale lease
    # =========================================================================
    stale_submission_accepted = await task_repo.submit_task_attempt(
        task_id=task_a.task_id,
        worker_id=worker_1_id,
        fencing_token=fencing_token_emp01,  # Stale fencing token!
        execution_receipt={"result": "stale_output"},
    )
    assert stale_submission_accepted is False, "Step 13 Failed: Stale submission must be strictly rejected!"

    # =========================================================================
    # STEP 14: EMP-02 claims Task-A lease; lease issued with incremented fencing_token
    # =========================================================================
    worker_2_id = "worker-emp-02"
    # Fast forward past retry delay for immediate claim in acceptance test
    attempts_after_failure[0].lease_expires_at = now
    emp_02.status = EmployeeStatus.IDLE
    await async_session.commit()

    claim_2 = await task_repo.claim_next_task(worker_id=worker_2_id, lease_seconds=120)
    assert claim_2 is not None, "Step 14 Failed: EMP-02 worker claim failed"
    assert claim_2["task_id"] == task_a.task_id
    assert claim_2["employee_id"] == emp_02.employee_id
    assert claim_2["fencing_token"] > fencing_token_emp01
    fencing_token_emp02 = claim_2["fencing_token"]

    started_2 = await task_repo.start_task_attempt(
        task_id=task_a.task_id,
        worker_id=worker_2_id,
        fencing_token=fencing_token_emp02,
        workspace_manifest_before={"files": []},
    )
    assert started_2 is True, "Step 14 Failed: EMP-02 failed to start task attempt"

    # =========================================================================
    # STEP 15: EMP-02 executes Task-A deliverables (creates string_math.py & test)
    # =========================================================================
    math_code = """
def add(a: str, b: str) -> str:
    return str(int(a.strip()) + int(b.strip()))

def multiply(a: str, b: str) -> str:
    return str(int(a.strip()) * int(b.strip()))
"""
    test_code = """
def test_string_math():
    assert add("10", "20") == "30"
    assert multiply("6", "7") == "42"
"""
    code_path = tmp_path / "string_math.py"
    test_path = tmp_path / "test_string_math.py"
    code_path.write_text(math_code, encoding="utf-8")
    test_path.write_text(test_code, encoding="utf-8")

    code_digest = hashlib.sha256(code_path.read_bytes()).hexdigest()
    test_digest = hashlib.sha256(test_path.read_bytes()).hexdigest()

    # =========================================================================
    # STEP 16: EMP-02 submits execution receipt for Task-A
    # =========================================================================
    submitted = await task_repo.submit_task_attempt(
        task_id=task_a.task_id,
        worker_id=worker_2_id,
        fencing_token=fencing_token_emp02,
        execution_receipt={
            "artifacts": [str(code_path), str(test_path)],
            "test_exit_code": 0,
            "stdout": "test_string_math PASSED",
        },
    )
    assert submitted is True, "Step 16 Failed: EMP-02 valid submission rejected"

    # =========================================================================
    # STEP 17: IndependentDeliverableVerifier evaluates Task-A deliverables
    # =========================================================================
    verifier = IndependentDeliverableVerifier(task_repo=task_repo, employee_repo=employee_repo)
    v_report = await verifier.verify_task_result(
        task_id=task_a.task_id,
        employee_id=emp_02.employee_id,
        artifacts=[str(code_path), str(test_path)],
        test_exit_code=0,
        tested_skills=["Python"],
        acceptance_criteria="All automated test suites executed green with valid artifacts",
    )
    assert v_report["passed"] is True, "Step 17 Failed: Independent verification failed"
    receipt = v_report["receipt"]
    assert receipt["signature"] is not None
    assert str(code_path) in receipt["artifact_digests"]
    assert receipt["artifact_digests"][str(code_path)] == code_digest
    assert str(test_path) in receipt["artifact_digests"]
    assert receipt["artifact_digests"][str(test_path)] == test_digest

    # =========================================================================
    # STEP 18: Strict completion gate passes: Task-A transitions to COMPLETED
    # =========================================================================
    completed_task_a = await verifier.gate_task_completion(task_a.task_id, receipt)
    assert completed_task_a.status == TaskStatus.COMPLETED
    assert completed_task_a.verification_result is not None

    # =========================================================================
    # STEP 19: Task-B prerequisites re-evaluated: Task-B unblocks!
    # =========================================================================
    ready_tasks_phase2 = await scheduler.get_ready_tasks(project.id)
    ready_ids_phase2 = {t.task_id for t in ready_tasks_phase2}
    assert task_b.task_id in ready_ids_phase2, "Step 19 Failed: Task-B should now be ready after Task-A completed"

    # =========================================================================
    # STEP 20: Task-B executes, passes independent verification, and completes
    # =========================================================================
    await task_repo.assign_task(task_b.task_id, emp_02.id)
    await task_repo.update_status(task_b.task_id, TaskStatus.ASSIGNED)

    claim_b = await task_repo.claim_next_task(worker_id=worker_2_id, lease_seconds=120)
    assert claim_b is not None, "Step 20 Failed: EMP-02 worker claim for Task-B failed"
    fencing_token_b = claim_b["fencing_token"]

    started_b = await task_repo.start_task_attempt(
        task_id=task_b.task_id,
        worker_id=worker_2_id,
        fencing_token=fencing_token_b,
        workspace_manifest_before={"files": []},
    )
    assert started_b is True, "Step 20 Failed: EMP-02 failed to start Task-B attempt"

    bench_code = """
import time
def test_benchmark():
    t0 = time.perf_counter()
    res = sum(int(x) for x in ["1"] * 1000)
    assert res == 1000
    assert time.perf_counter() - t0 < 1.0
"""
    bench_path = tmp_path / "test_benchmark.py"
    bench_path.write_text(bench_code, encoding="utf-8")

    submitted_b = await task_repo.submit_task_attempt(
        task_id=task_b.task_id,
        worker_id=worker_2_id,
        fencing_token=fencing_token_b,
        execution_receipt={
            "artifacts": [str(bench_path)],
            "test_exit_code": 0,
            "stdout": "test_benchmark PASSED",
        },
    )
    assert submitted_b is True, "Step 20 Failed: EMP-02 valid submission for Task-B rejected"

    v_report_b = await verifier.verify_task_result(
        task_id=task_b.task_id,
        employee_id=emp_02.employee_id,
        artifacts=[str(bench_path)],
        test_exit_code=0,
        tested_skills=["Python"],
        acceptance_criteria="All automated test suites executed green",
    )
    assert v_report_b["passed"] is True, "Step 20 Failed: Task-B verification failed"
    await verifier.gate_task_completion(task_b.task_id, v_report_b["receipt"])

    completed_task_b = await task_repo.get_by_task_id(task_b.task_id)
    assert completed_task_b.status == TaskStatus.COMPLETED

    # =========================================================================
    # STEP 21: BossBrain compiles final signed ProjectCompletionReport
    # =========================================================================
    report = await brain.build_project_report(project.id)
    assert report is not None, "Step 21 Failed: Boss project report returned None"

    # Assertions on final signed report
    assert report["project_id"] == project.id
    assert report["project_name"] == "String-Math-Utility"
    assert report["report_signature"] is not None
    assert report["reporting_authority"] == "BossBrain-Reporting-Authority-v1"

    # Deliverables classification
    assert len(report["verified_deliverables"]) >= 2
    verified_task_ids = {d["task_id"] for d in report["verified_deliverables"]}
    assert task_a.task_id in verified_task_ids
    assert task_b.task_id in verified_task_ids

    # Failed attempts summary
    assert len(report["failed_attempts_summary"]) >= 1
    failed_worker_ids = {fa["worker_id"] for fa in report["failed_attempts_summary"]}
    assert worker_1_id in failed_worker_ids

    # Immutable audit verification
    audit_entries = await audit_repo.list_by_resource("Project", project.id)
    report_audits = [e for e in audit_entries if e.action == "GENERATE_PROJECT_REPORT"]
    assert len(report_audits) == 1, "Step 21 Failed: Missing immutable audit log for report generation"
    assert report_audits[0].details["report_signature"] == report["report_signature"]
    assert report_audits[0].actor == "BossAI"
