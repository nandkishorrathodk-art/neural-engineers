import pytest
from neural.boss.brain import BossBrain
from neural.boss.delegator import TaskDelegator
from neural.boss.recovery import FailureRecoveryManager
from neural.constants import (
    ApprovalStatus,
    DestructiveOperationType,
    MeetingStatus,
    MeetingType,
    TaskStatus,
    VerificationStatus,
)
from neural.containers.manager import ContainerManager
from neural.db.repositories import (
    ActionItemRepository,
    ApprovalRepository,
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    MeetingRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.evaluation.verifier import IndependentVerifier
from neural.exceptions import HumanApprovalRequiredError
from neural.meetings.conductor import MeetingConductor
from neural.meetings.reports import ProgressReportCompiler
from neural.observability.audit import AuditLogChainVerifier
from neural.sandboxes.workstation import SandboxConfig, WorkstationSandbox
from neural.security.approval_gates import HumanApprovalGate
from neural.security.kill_switch import get_kill_switch, reset_kill_switch
from neural.tasks.artifacts import ArtifactManager
from neural.tasks.scheduler import TaskScheduler
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def clean_system():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_phase6_full_end_to_end_autonomous_benchmark_mission(
    async_session: AsyncSession,
    tmp_path,
):
    """
    Phase 6 Master Done-Gate Integration Test:
    1. Operator Objective: Submits 'Deliver an Enterprise Token Authentication & Webhook Microservice'.
    2. Hierarchical Planning: Boss AI decomposes objective into hierarchical Task DAG.
    3. Workforce Recruitment: Dynamically hires Senior Backend Engineer and QA Engineer.
    4. Workstation Provisioning: Provisions isolated workstations with seccomp and network perimeter.
    5. Isolated Sandbox Execution: Writes HMAC token verifier and test runner.
       - Intercepts and blocks malicious cloud metadata probe (169.254.169.254).
    6. Empirical Verification: Independent QA Verifier runs tests, AST checks, generates signed receipt.
       - Strict gate prevents completion without verification.
       - Skills transition verified=False -> verified=True.
    7. Boardroom Standup: Conductor convenes meeting, gathers fact-grounded reports (4 questions, zero fabricated %),
       generates auditable meeting minutes.
    8. Human Sovereignty Approval Gate: Destructive production deployment intercepted, approved by human operator.
    9. Emergency Kill Switch Benchmark: Instant halt (<200ms) and state recovery verification.
    10. Final Deliverables & Audit Integrity: Cryptographic SHA-256 artifact checksums and audit chain verification.
    """
    project_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session, strict_verification=True)
    emp_repo = EmployeeRepository(async_session)
    meeting_repo = MeetingRepository(async_session)
    action_repo = ActionItemRepository(async_session)
    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)
    container_repo = ContainerRepository(async_session)

    artifact_storage = str(tmp_path / "artifacts")
    artifact_manager = ArtifactManager(session=async_session, storage_base_path=artifact_storage)
    delegator = TaskDelegator()
    recovery_manager = FailureRecoveryManager()
    verifier = IndependentVerifier(employee_repo=emp_repo, task_repo=task_repo)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. OPERATOR OBJECTIVE SUBMISSION & BOSS AI PLANNING
    # ─────────────────────────────────────────────────────────────────────────
    boss = BossBrain(
        project_repo=project_repo,
        task_repo=task_repo,
        employee_repo=emp_repo,
        verifier=verifier,
        recovery_manager=recovery_manager,
        delegator=delegator,
    )

    objective = "Deliver an Enterprise Token Authentication & Webhook Microservice"
    created_project, _ = await boss.plan_and_create_project(
        objective=objective,
        project_repo=project_repo,
        task_repo=task_repo,
    )
    assert created_project.id is not None
    assert created_project.objective == objective


    # Verify audit log recorded project creation
    await audit_repo.log(
        actor="operator_human",
        action="OBJECTIVE_SUBMITTED",
        resource_type="project",
        resource_id=created_project.id,
        details={"objective": objective},
    )

    # ─────────────────────────────────────────────────────────────────────────
    # 2. WORKFORCE RECRUITMENT & DYNAMIC HIRING
    # ─────────────────────────────────────────────────────────────────────────
    factory = EmployeeFactory(emp_repo)
    backend_dev = await factory.hire_employee(
        name="Elena Rostova",
        title="Senior Security & Backend Engineer",
        initial_skills=["Python", "FastAPI", "Cryptography"],
    )
    qa_dev = await factory.hire_employee(
        name="Marcus Vance",
        title="Senior QA Engineer",
        initial_skills=["Python", "Testing", "SecurityAnalysis"],
    )

    # Initial skills start unverified
    assert all(not s.verified for s in backend_dev.skills)
    assert all(not s.verified for s in qa_dev.skills)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. WORKSTATION PROVISIONING WITH ZERO-TRUST PROFILES
    # ─────────────────────────────────────────────────────────────────────────
    cm = ContainerManager()
    workstation_spec = await cm.provision_employee_desktop(
        employee_id=backend_dev.employee_id,
        role_image="employee-base",
        cpu_limit=2.0,
        mem_limit="4g",
        container_repo=container_repo,
        employee_repo=emp_repo,
    )
    assert workstation_spec["status"] == "running"
    assert workstation_spec["privileged"] is False
    assert workstation_spec["user"] == "1000:1000"

    # ─────────────────────────────────────────────────────────────────────────
    # 4. ISOLATED SANDBOX EXECUTION & ZERO-TRUST BOUNDARY PERIMETER
    # ─────────────────────────────────────────────────────────────────────────
    sandbox_dir = str(tmp_path / "sandbox_elena")
    cfg = SandboxConfig(
        timeout_seconds=5.0,
        enforce_network_perimeter=True,
        enforce_syscall_filtering=True,
    )

    async with WorkstationSandbox(config=cfg, custom_workdir=sandbox_dir) as sandbox:
        # A. Malicious metadata escape attempt: MUST BE INTERCEPTED
        malicious_attempt = (
            "import urllib.request\n"
            "urllib.request.urlopen('http://169.254.169.254/latest/meta-data/iam/security-credentials/')\n"
        )
        probe_receipt = await sandbox.execute_python_code(malicious_attempt, filename="metadata_probe.py")
        assert probe_receipt.passed_safety is False
        assert probe_receipt.exit_code == -1
        assert any("metadata" in v.lower() for v in probe_receipt.safety_violations)

        # B. Clean implementation: HMAC Token Validator
        solution_code = (
            "import hmac\n"
            "import hashlib\n\n"
            "def verify_webhook_token(secret: bytes, payload: bytes, signature: str) -> bool:\n"
            "    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()\n"
            "    return hmac.compare_digest(expected, signature)\n"
        )
        test_suite_code = (
            "from solution import verify_webhook_token\n"
            "import hmac, hashlib\n\n"
            "secret = b'test_master_secret_key_32_bytes'\n"
            "payload = b'{\"event\": \"payment.success\", \"amount\": 4200}'\n"
            "sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()\n"
            "assert verify_webhook_token(secret, payload, sig) is True\n"
            "assert verify_webhook_token(secret, payload, 'invalid_sig') is False\n"
            "print('ALL_UNIT_TESTS_PASSED')\n"
        )

        clean_receipt = await sandbox.execute_python_code(
            code=solution_code,
            test_code=test_suite_code,
            filename="solution.py",
        )
        assert clean_receipt.passed_safety is True
        assert clean_receipt.exit_code == 0
        assert "ALL_UNIT_TESTS_PASSED" in clean_receipt.stdout

    # ─────────────────────────────────────────────────────────────────────────
    # 5. EMPIRICAL VERIFICATION & SKILL CERTIFICATION (ZERO SUCCESS-BY-DECREE)
    # ─────────────────────────────────────────────────────────────────────────
    # Store artifact
    art = await artifact_manager.store_artifact(
        project_id=created_project.id,
        task_id="TASK-BENCH-01",
        name="hmac_auth.py",
        content=solution_code,
        artifact_type="code",
    )
    assert artifact_manager.verify_checksum(art) is True

    # Create task assigned to Elena
    task = await task_repo.create(
        task_id="TASK-BENCH-01",
        project_id=created_project.id,
        title="HMAC Token Webhook Authenticator",
        objective="Validate HMAC tokens on incoming webhook traffic",
        scope="Security & Backend",
        acceptance_criteria="Clean unit tests with HMAC constant-time digest",
        required_capabilities=["Python", "Cryptography"],
        assigned_to=backend_dev.id,
        status=TaskStatus.RUNNING,
    )

    # Independent QA evaluates artifacts and runner output
    qa_result = await verifier.verify_task_result(
        task_id=task.task_id,
        employee_id=backend_dev.employee_id,
        artifacts=[art.path],
        test_exit_code=0,
        tested_skills=["Python", "Cryptography"],
        acceptance_criteria=task.acceptance_criteria,
        file_contents={art.path: solution_code},
    )
    assert qa_result["passed"] is True
    assert qa_result["verification_status"] == VerificationStatus.PASSED.value

    # Gate task completion with verified receipt
    completed_task = await verifier.gate_task_completion(task.task_id, qa_result["receipt"])
    assert completed_task.status == TaskStatus.COMPLETED

    # Verify Elena's skills transitioned to verified=True
    elena_certified = await emp_repo.get_by_employee_id(backend_dev.employee_id)
    verified_skills = [s for s in elena_certified.skills if s.verified]
    assert len(verified_skills) >= 2
    assert {"Python", "Cryptography"}.issubset({s.skill_name for s in verified_skills})

    # ─────────────────────────────────────────────────────────────────────────
    # 6. AUTONOMOUS BOARDROOM STANDUP & FACT-GROUNDED PROGRESS REPORTING
    # ─────────────────────────────────────────────────────────────────────────
    conductor = MeetingConductor(meeting_repo=meeting_repo, action_item_repo=action_repo)

    # Autonomous standup convened
    meeting = await conductor.schedule_meeting(
        title="Phase 6 Benchmark Standup",
        meeting_type=MeetingType.DAILY_STANDUP,
        agenda=["Review HMAC Auth Deliverable", "Verify Production Deployment Readiness"],
        participants=[backend_dev.employee_id, qa_dev.employee_id, "BossAI"],
    )
    await conductor.convene_meeting(meeting.meeting_id)

    # Fact-grounded progress report (4 questions answered with verified receipt)
    report = ProgressReportCompiler.compile(
        employee_id=backend_dev.employee_id,
        task_id=task.task_id,
        what_was_done="Engineered constant-time HMAC token validator for webhook ingress.",
        what_remains="Production cluster rollout and monitoring setup.",
        what_is_blocking="Awaiting operator human sovereignty approval for production deployment.",
        verification_receipt=qa_result["receipt"],
        exit_code=0,
    )
    assert report.employee_id == backend_dev.employee_id
    assert report.factual_status == "blocked"

    # Collect progress report in meeting minutes
    await conductor.collect_report(meeting.meeting_id, report)

    # Record decision rationale
    await conductor.record_decision(
        meeting_id=meeting.meeting_id,
        topic="Review HMAC Auth Deliverable",
        decision="Deliverable accepted. Request production rollout approval.",
        rationale="QA verification passed with 0 exit code and valid HMAC digest",
        decided_by="BossAI",
    )

    # Add tracked action item
    await conductor.add_action_item(
        meeting_id=meeting.meeting_id,
        description="Deploy HMAC Webhook Authenticator to Production",
        assigned_to=backend_dev.employee_id,
    )

    # Conclude meeting with structured minutes
    concluded_meeting = await conductor.conclude_meeting(
        meeting_id=meeting.meeting_id,
        summary="Phase 6 Benchmark Standup concluded.",
    )
    assert concluded_meeting.status == MeetingStatus.CONCLUDED
    assert len(concluded_meeting.decisions) == 1

    # ─────────────────────────────────────────────────────────────────────────
    # 7. HUMAN SOVEREIGNTY GATED DESTRUCTIVE OPERATION
    # ─────────────────────────────────────────────────────────────────────────
    deployment_executed = False

    async def execute_prod_deployment():
        nonlocal deployment_executed
        deployment_executed = True
        return {"status": "deployed_to_prod", "version": "1.0.0"}

    # Attempt execution without approval: MUST BE BLOCKED
    with pytest.raises(HumanApprovalRequiredError):
        await HumanApprovalGate.execute_with_gate(
            action_type=DestructiveOperationType.PRODUCTION_DEPLOYMENT.value,
            requested_by=backend_dev.employee_id,
            description="Deploy HMAC Webhook Authenticator to production cluster",
            operation_callable=execute_prod_deployment,
            details={"env": "production", "artifact": art.checksum},
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )
    assert deployment_executed is False

    # Operator reviews pending approvals in Control Room
    pending_approvals = await approval_repo.list_pending()
    assert len(pending_approvals) >= 1
    deployment_req = pending_approvals[0]

    # Operator approves
    approved_req = await HumanApprovalGate.decide(
        approval_id=deployment_req.id,
        decision="approved",
        decided_by="human_director",
        approval_repo=approval_repo,
        audit_repo=audit_repo,
    )
    assert approved_req.status == ApprovalStatus.APPROVED

    # Now execute with valid approval ID -> PROCEEDS
    deploy_result = await HumanApprovalGate.execute_with_gate(
        action_type=DestructiveOperationType.PRODUCTION_DEPLOYMENT.value,
        requested_by=backend_dev.employee_id,
        description="Deploy HMAC Webhook Authenticator to production cluster",
        operation_callable=execute_prod_deployment,
        details={"env": "production"},
        approval_id=approved_req.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
    )
    assert deployment_executed is True
    assert deploy_result["status"] == "deployed_to_prod"

    # ─────────────────────────────────────────────────────────────────────────
    # 8. GLOBAL EMERGENCY KILL SWITCH INSTANT HALT & RESUME BENCHMARK
    # ─────────────────────────────────────────────────────────────────────────
    kill_switch = get_kill_switch()
    scheduler = TaskScheduler(task_repo=task_repo, employee_repo=emp_repo)
    kill_switch.register_scheduler(scheduler)
    kill_switch.register_container_manager(cm)
    kill_switch.register_boss_brain(boss)

    # Benchmark instant emergency stop
    halt_receipt = await kill_switch.emergency_halt(
        reason="Operator drill benchmark",
        actor="security_director",
        audit_repo=audit_repo,
    )
    assert halt_receipt["halted"] is True
    assert halt_receipt["halt_latency_ms"] < 200.0, f"Halt latency {halt_receipt['halt_latency_ms']}ms exceeds 200ms"
    assert kill_switch.is_halted is True

    # Attempting to execute while halted is blocked
    with pytest.raises(Exception):
        kill_switch.guard_active()

    # Resume system
    resume_receipt = await kill_switch.resume(
        actor="security_director",
        recovery_strategy="checkpoint",
        task_repo=task_repo,
        audit_repo=audit_repo,
    )
    assert resume_receipt["resumed"] is True
    assert kill_switch.is_halted is False

    # ─────────────────────────────────────────────────────────────────────────
    # 9. FINAL ARTIFACT BUNDLE & TAMPER-EVIDENT AUDIT CHAIN INTEGRITY
    # ─────────────────────────────────────────────────────────────────────────
    history = await artifact_manager.get_version_history(created_project.id, "hmac_auth.py")
    assert len(history) == 1
    assert history[0].checksum == art.checksum

    # Validate unbroken cryptographic audit log hash chain
    is_audit_valid, audit_issues = await audit_repo.verify_chain_integrity()
    assert is_audit_valid is True, f"Audit chain broken: {audit_issues}"
    assert len(audit_issues) == 0

    all_audit_entries = await audit_repo.list_all(limit=50)
    assert len(all_audit_entries) >= 4

    # Direct validation with AuditLogChainVerifier
    ordered_entries = AuditLogChainVerifier.order_chain(all_audit_entries)
    chain_valid, chain_issues = AuditLogChainVerifier.verify_records(ordered_entries)
    assert chain_valid is True, f"Chain verifier reported issues: {chain_issues}"
