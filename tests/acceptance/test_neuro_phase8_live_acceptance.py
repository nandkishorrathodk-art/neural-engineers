"""
ANCOS NEURO-8 Live Acceptance Test Suite.
Empirically verifies Autonomous Engineering & Software Development across:
1. Owner Authentication, Budget Setup & Repositories Initialization.
2. Dynamic Workforce Recruitment (Developer, QA Engineer, Security Reviewer with unverified skills).
3. Project & Task DAG Decomposition.
4. Isolated Git Worktree Lifecycle (EngineeringWorktreeManager, pinned base commits, SHA-256 provenance receipts).
5. Developer Feature Implementation with Injected Regression (matrix mathematics utility).
6. Independent QA Review & Automated Failure Detection (QAReviewEngine detects regression, generates BugReport).
7. Automated Bug Remediation Loop & QA Re-Verification (Developer fixes bug, QA verifies green test suite).
8. Independent Security Review (SecurityReviewEngine scans AST, detects 0 vulnerabilities, verifies score >= 0.99).
9. Approval-Gated Integration & Clean Merge (fails closed without approval, merges cleanly with HumanApprovalGate).
10. Empirical Skill Certification & Cryptographic Audit Hash-Chain Integrity (verified=True promotion, AuditLogChainVerifier).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from neural.constants import (
    ApprovalStatus,
    DestructiveOperationType,
    ProjectStatus,
    TaskPriority,
    TaskStatus,
)
from neural.db.models import (
    BudgetAccount,
    Project,
    Task,
)
from neural.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    BudgetRepository,
    EmployeeRepository,
    TaskRepository,
)
from neural.engineering.models import (
    EngineeringStage,
    QAReviewReceipt,
    ReviewGateError,
)
from neural.engineering.pipeline import CollaborativeEngineeringPipeline
from neural.exceptions import HumanApprovalRequiredError
from neural.observability.audit import AuditLogChainVerifier
from neural.security.kill_switch import reset_kill_switch
from sqlalchemy.ext.asyncio import AsyncSession


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _setup_git_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "source_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "ANCOS Master Repo")
    _git(repo, "config", "user.email", "master@ancos.corp")
    (repo / "README.md").write_text("# ANCOS Engineering Enterprise\n\nInitial repository baseline.\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial baseline commit")
    return repo


@pytest.fixture(autouse=True)
def reset_system_safety_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_neuro_phase8_live_acceptance_full_scenario(
    async_session: AsyncSession,
    tmp_path: Path,
):
    """
    NEURO-8 Live Acceptance Scenario:
    Demonstrates the complete Autonomous Engineering & Software Development lifecycle:
    dynamic multi-employee staffing, isolated worktree provisioning with cryptographic provenance,
    developer implementation, QA automated regression detection with structured bug reports,
    bug remediation and re-verification, independent security AST & vulnerability review,
    approval-gated merge strictly guarded by HumanApprovalGate, empirical skill certification,
    and cryptographic audit hash-chain integrity verification.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # =========================================================================
    # STEP 1: Owner Authentication, Budget Setup & Repositories Initialization
    # =========================================================================
    owner_identity = "owner@ancos.corp"
    assert "@" in owner_identity

    budget_repo = BudgetRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    month_scope = f"organization-month:{now:%Y-%m}"
    account = BudgetAccount(
        scope_key=month_scope,
        cap_usd=Decimal("500.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    quota = await budget_repo.get_available_quota(month_scope)
    assert quota >= Decimal("100.00"), f"Step 1 Failed: Insufficient monthly quota: {quota}"

    reservation = await budget_repo.reserve(
        idempotency_key="plan-reserve-neuro8-acceptance",
        reserved_usd=Decimal("2.50"),
        scope_caps={month_scope: Decimal("500.00")},
    )
    assert reservation is not None and reservation.status == "reserved", "Step 1 Failed: Budget pre-reservation"

    await audit_repo.log(
        actor=owner_identity,
        action="OWNER_AUTHENTICATED",
        resource_type="budget",
        resource_id=month_scope,
        details={"allocated_usd": "500.00", "phase": "NEURO-8"},
    )

    # =========================================================================
    # STEP 2: Dynamic Engineering Workforce Recruitment & Skill Initialization
    # =========================================================================
    from neural.employees.factory import EmployeeFactory

    factory = EmployeeFactory(employee_repo)
    factory.settings.EMPLOYEE_WORKSPACE_BASE = str(tmp_path / "workspaces")

    # 1. Developer Employee
    dev_emp = await factory.recruit_dynamic_employee(
        role_title="Senior Software Engineer",
        department="Engineering",
        initial_skills=["python", "software_development", "git"],
    )

    # 2. QA Engineer
    qa_emp = await factory.recruit_dynamic_employee(
        role_title="Lead QA Engineer",
        department="Quality & Reliability",
        initial_skills=["test_automation", "qa_verification"],
    )

    # 3. Security Reviewer
    sec_emp = await factory.recruit_dynamic_employee(
        role_title="Security Architect",
        department="Information Security",
        initial_skills=["security_audit", "vulnerability_assessment"],
    )

    # Initial skills start strictly UNVERIFIED (verified=False)
    for emp in [dev_emp, qa_emp, sec_emp]:
        assert len(emp.skills) >= 2, f"Step 2 Failed: Employee {emp.employee_id} missing initial skills"
        for s in emp.skills:
            assert s.verified is False, f"Step 2 Failed: Skill {s.skill_name} must start unverified"

    await audit_repo.log(
        actor="EmployeeFactory",
        action="WORKFORCE_RECRUITED",
        resource_type="workforce",
        resource_id=dev_emp.employee_id,
        details={"employees": [dev_emp.employee_id, qa_emp.employee_id, sec_emp.employee_id]},
    )

    # =========================================================================
    # STEP 3: Project & Task DAG Decomposition
    # =========================================================================
    project = Project(
        name="Neural-Matrix-Math",
        objective="Autonomous development of resilient matrix mathematics utility",
        status=ProjectStatus.ACTIVE,
    )
    async_session.add(project)
    await async_session.commit()

    task_id = "TASK-ENG-08"
    task = Task(
        task_id=task_id,
        project_id=project.id,
        assigned_to=dev_emp.id,
        title="Implement matrix mathematics utility with independent verification",
        objective="Develop matrix operations: addition, scalar multiplication, 2x2 determinant with tests",
        scope="Matrix operations library and comprehensive pytest test suite",
        status=TaskStatus.ASSIGNED,
        priority=TaskPriority.HIGH,
        acceptance_criteria="All matrix functions must pass independent unit test verification and security AST review",
    )
    async_session.add(task)
    await async_session.commit()

    await audit_repo.log(
        actor="BossBrain",
        action="TASK_ASSIGNED",
        resource_type="task",
        resource_id=task_id,
        details={"project_id": project.id, "assigned_to": dev_emp.employee_id},
    )

    # =========================================================================
    # STEP 4: Isolated Git Worktree Lifecycle & Provenance Verification
    # =========================================================================
    source_repo = _setup_git_repository(tmp_path)
    managed_worktrees = tmp_path / "data" / "worktrees"
    pipeline = CollaborativeEngineeringPipeline(source_repo, managed_worktrees)

    initial_head = _git(source_repo, "rev-parse", "HEAD")
    worktree_receipt = pipeline.provision_worktree(task_id, base_ref="HEAD")

    assert worktree_receipt.task_id == task_id
    assert worktree_receipt.branch == f"ancos/task/{task_id}"
    assert worktree_receipt.base_commit == initial_head
    assert worktree_receipt.path.is_dir()
    assert (managed_worktrees / f"{task_id}.receipt.json").is_file()

    # Cryptographically verify the worktree provenance
    verified_worktree = pipeline.worktree_manager.verify_receipt(task_id)
    assert verified_worktree == worktree_receipt
    assert verified_worktree.receipt_sha256 == worktree_receipt.receipt_sha256

    # Verify source checkout remains pristine on main
    assert _git(source_repo, "branch", "--show-current") == "main"
    assert _git(source_repo, "rev-parse", "HEAD") == initial_head

    await audit_repo.log(
        actor="EngineeringWorktreeManager",
        action="WORKTREE_PROVISIONED",
        resource_type="worktree",
        resource_id=task_id,
        details={
            "branch": worktree_receipt.branch,
            "base_commit": worktree_receipt.base_commit,
            "receipt_sha256": worktree_receipt.receipt_sha256,
        },
    )

    # =========================================================================
    # STEP 5: Developer Implementation with Injected Regression
    # =========================================================================
    # Elena implements the matrix math package, but introduces an edge-case bug in determinant
    buggy_matrix_code = (
        "def add_matrices(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:\n"
        "    return [[a[r][c] + b[r][c] for c in range(len(a[0]))] for r in range(len(a))]\n\n"
        "def scalar_multiply(m: list[list[float]], s: float) -> list[list[float]]:\n"
        "    return [[val * s for val in row] for row in m]\n\n"
        "def determinant_2x2(m: list[list[float]]) -> float:\n"
        "    # Injected Failure: computes a*d + b*c instead of a*d - b*c\n"
        "    return (m[0][0] * m[1][1]) + (m[0][1] * m[1][0])\n"
    )
    test_code = (
        "from matrix_math import add_matrices, scalar_multiply, determinant_2x2\n\n"
        "def test_matrix_addition():\n"
        "    a = [[1.0, 2.0], [3.0, 4.0]]\n"
        "    b = [[5.0, 6.0], [7.0, 8.0]]\n"
        "    assert add_matrices(a, b) == [[6.0, 8.0], [10.0, 12.0]]\n\n"
        "def test_scalar_multiplication():\n"
        "    m = [[1.0, -2.0], [3.0, 0.0]]\n"
        "    assert scalar_multiply(m, 3.0) == [[3.0, -6.0], [9.0, 0.0]]\n\n"
        "def test_determinant_2x2():\n"
        "    # [[4, 6], [3, 8]] -> 4*8 - 6*3 = 32 - 18 = 14.0\n"
        "    m = [[4.0, 6.0], [3.0, 8.0]]\n"
        "    assert determinant_2x2(m) == 14.0\n"
    )

    dev_commit = pipeline.apply_developer_changes(
        task_id=task_id,
        files={"matrix_math.py": buggy_matrix_code, "test_matrix_math.py": test_code},
        commit_message="Implement matrix addition, scalar multiplication, and determinant with tests",
        developer_id=dev_emp.employee_id,
    )
    assert dev_commit != initial_head
    assert pipeline.get_stage(task_id) == EngineeringStage.QA_REVIEW

    await audit_repo.log(
        actor=dev_emp.employee_id,
        action="CODE_COMMITTED",
        resource_type="worktree",
        resource_id=task_id,
        details={"commit_hash": dev_commit, "message": "Initial matrix_math implementation"},
    )

    # =========================================================================
    # STEP 6: Independent QA Review & Automated Failure Detection
    # =========================================================================
    # Quinn Adams (QA) inspects the worktree diff and executes the test runner in an isolated sandbox
    qa_receipt = pipeline.run_qa_review(
        task_id=task_id,
        qa_engineer_id=qa_emp.employee_id,
    )

    assert not qa_receipt.passed, "Step 6 Failed: QA must detect the injected determinant calculation bug"
    assert qa_receipt.test_exit_code != 0, "Step 6 Failed: Pytest runner must exit with non-zero exit code"
    assert qa_receipt.bug_report is not None, "Step 6 Failed: Structured BugReport must be generated"
    assert qa_receipt.bug_report.task_id == task_id
    assert qa_receipt.bug_report.reporter_id == qa_emp.employee_id
    assert any("test_determinant_2x2" in t for t in qa_receipt.bug_report.failed_tests)
    assert qa_receipt.verify_signature() is True, "Step 6 Failed: QA failure receipt signature must be valid"
    assert pipeline.get_stage(task_id) == EngineeringStage.IMPLEMENTATION

    await audit_repo.log(
        actor=qa_emp.employee_id,
        action="QA_FAILURE_DETECTED",
        resource_type="task",
        resource_id=task_id,
        details={
            "qa_receipt_id": qa_receipt.receipt_id,
            "bug_id": qa_receipt.bug_report.bug_id,
            "failed_tests": qa_receipt.bug_report.failed_tests,
        },
    )

    # =========================================================================
    # STEP 7: Automated Bug Remediation Loop & QA Re-Verification
    # =========================================================================
    # Elena receives the BugReport, identifies the sign error in determinant_2x2, and fixes it
    fixed_matrix_code = (
        "def add_matrices(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:\n"
        "    return [[a[r][c] + b[r][c] for c in range(len(a[0]))] for r in range(len(a))]\n\n"
        "def scalar_multiply(m: list[list[float]], s: float) -> list[list[float]]:\n"
        "    return [[val * s for val in row] for row in m]\n\n"
        "def determinant_2x2(m: list[list[float]]) -> float:\n"
        "    # Bug Remediation: correct formula a*d - b*c\n"
        "    return (m[0][0] * m[1][1]) - (m[0][1] * m[1][0])\n"
    )

    fix_commit = pipeline.remediate_bug(
        task_id=task_id,
        bug_report=qa_receipt.bug_report,
        fix_files={"matrix_math.py": fixed_matrix_code},
        commit_message="Fix matrix determinant calculation formula (a*d - b*c)",
        developer_id=dev_emp.employee_id,
    )
    assert fix_commit != dev_commit

    await audit_repo.log(
        actor=dev_emp.employee_id,
        action="BUG_REMEDIATION_COMMITTED",
        resource_type="worktree",
        resource_id=task_id,
        details={"bug_id": qa_receipt.bug_report.bug_id, "fix_commit": fix_commit},
    )

    # Quinn Adams re-runs independent QA verification
    re_qa_receipt = pipeline.run_qa_review(
        task_id=task_id,
        qa_engineer_id=qa_emp.employee_id,
    )

    assert re_qa_receipt.passed is True, "Step 7 Failed: QA review must pass after bug remediation"
    assert re_qa_receipt.test_exit_code == 0
    assert re_qa_receipt.tests_passed >= 3
    assert re_qa_receipt.tests_failed == 0
    assert re_qa_receipt.bug_report is None
    assert re_qa_receipt.verify_signature() is True
    assert "matrix_math.py" in re_qa_receipt.artifact_digests
    assert "test_matrix_math.py" in re_qa_receipt.artifact_digests
    assert pipeline.get_stage(task_id) == EngineeringStage.SECURITY_REVIEW

    await audit_repo.log(
        actor=qa_emp.employee_id,
        action="QA_REVIEW_PASSED",
        resource_type="task",
        resource_id=task_id,
        details={"qa_receipt_id": re_qa_receipt.receipt_id, "tests_passed": re_qa_receipt.tests_passed},
    )

    # =========================================================================
    # STEP 8: Independent Security Review & Vulnerability/Secret Scanning
    # =========================================================================
    # Sergei Volkov (Security) performs AST security and secret scanning
    sec_receipt = pipeline.run_security_review(
        task_id=task_id,
        security_engineer_id=sec_emp.employee_id,
    )

    assert sec_receipt.passed is True, "Step 8 Failed: Clean matrix math code must pass security review"
    assert sec_receipt.security_score >= 0.99
    assert len(sec_receipt.vulnerabilities_found) == 0
    assert sec_receipt.verify_signature() is True
    assert pipeline.get_stage(task_id) == EngineeringStage.APPROVAL_GATE

    await audit_repo.log(
        actor=sec_emp.employee_id,
        action="SECURITY_REVIEW_PASSED",
        resource_type="task",
        resource_id=task_id,
        details={"security_receipt_id": sec_receipt.receipt_id, "score": sec_receipt.security_score},
    )

    # =========================================================================
    # STEP 9: Approval-Gated Integration & Sovereignty Gate Enforcement
    # =========================================================================
    # 9a. Pre-merge fail-closed verification: merging WITHOUT valid approval fails closed!
    with pytest.raises(HumanApprovalRequiredError) as exc_info:
        await pipeline.execute_merge(
            task_id=task_id,
            target_branch="main",
            qa_receipt=re_qa_receipt,
            security_receipt=sec_receipt,
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )
    assert "branch_merge" in str(exc_info.value) or "destructive" in str(exc_info.value).lower()
    # Confirm main branch HEAD is completely untouched
    assert _git(source_repo, "rev-parse", "HEAD") == initial_head

    # 9b. Verify tampered receipt rejection
    tampered_qa_receipt = QAReviewReceipt(
        receipt_id=re_qa_receipt.receipt_id,
        task_id=re_qa_receipt.task_id,
        qa_engineer_id=re_qa_receipt.qa_engineer_id,
        passed=re_qa_receipt.passed,
        test_exit_code=re_qa_receipt.test_exit_code,
        tests_run=re_qa_receipt.tests_run,
        tests_passed=re_qa_receipt.tests_passed,
        tests_failed=re_qa_receipt.tests_failed,
        diff_stat=re_qa_receipt.diff_stat,
        artifact_digests=re_qa_receipt.artifact_digests,
        timestamp=re_qa_receipt.timestamp,
        signature="badf00d" * 8,  # Forged signature
        bug_report=None,
    )
    with pytest.raises(ReviewGateError, match="signature verification failed"):
        await pipeline.execute_merge(
            task_id=task_id,
            target_branch="main",
            qa_receipt=tampered_qa_receipt,
            security_receipt=sec_receipt,
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )

    # 9c. Human Operator / Boss creates and approves the destructive merge request
    approval = await approval_repo.create(
        action_type=DestructiveOperationType.BRANCH_MERGE.value,
        description=f"Approve integration merge of task {task_id} into main branch",
        justification="Verified by independent QA pass and Security AST scan",
        requested_by=dev_emp.employee_id,
        details={
            "task_id": task_id,
            "qa_receipt_id": re_qa_receipt.receipt_id,
            "security_receipt_id": sec_receipt.receipt_id,
        },
    )
    assert approval.status == ApprovalStatus.PENDING

    await approval_repo.decide(
        approval_id=approval.id,
        decision="approved",
        decided_by=owner_identity,
    )
    reloaded_approval = await approval_repo.get_by_id(approval.id)
    assert reloaded_approval is not None and reloaded_approval.status == ApprovalStatus.APPROVED

    await audit_repo.log(
        actor=owner_identity,
        action="APPROVAL_GRANTED",
        resource_type="approval",
        resource_id=approval.id,
        details={"decision": "approved", "action_type": DestructiveOperationType.BRANCH_MERGE.value},
    )

    # 9d. Clean merge execution
    merge_receipt = await pipeline.execute_merge(
        task_id=task_id,
        target_branch="main",
        qa_receipt=re_qa_receipt,
        security_receipt=sec_receipt,
        approval_id=approval.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
        requested_by=owner_identity,
        delete_task_branch=False,
    )

    assert merge_receipt.task_id == task_id
    assert merge_receipt.target_branch == "main"
    assert merge_receipt.verify_signature() is True
    assert (managed_worktrees / f"{task_id}.merge.json").is_file()

    # Confirm target main repository has been advanced with merged files
    new_head = _git(source_repo, "rev-parse", "HEAD")
    assert new_head != initial_head
    assert (source_repo / "matrix_math.py").is_file()
    assert (source_repo / "test_matrix_math.py").is_file()
    assert "determinant_2x2" in (source_repo / "matrix_math.py").read_text(encoding="utf-8")

    # Confirm worktree folder is cleanly removed
    assert not worktree_receipt.path.exists()
    assert pipeline.get_stage(task_id) == EngineeringStage.COMPLETED

    # Update task in database using passing verification receipt
    task_receipt = {
        "task_id": task_id,
        "passed": True,
        "verifier_id": "IndependentDeliverableVerifier",
        "qa_receipt_id": re_qa_receipt.receipt_id,
        "security_receipt_id": sec_receipt.receipt_id,
        "merge_receipt_id": merge_receipt.receipt_id,
        "timestamp": now.isoformat(),
    }
    canonical = json.dumps(task_receipt, sort_keys=True, default=str)
    task_receipt["signature"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    await task_repo.complete_with_receipt(task_id, task_receipt)

    completed_task = await task_repo.get_by_task_id(task_id)
    assert completed_task is not None and completed_task.status == TaskStatus.COMPLETED

    # =========================================================================
    # STEP 10: Empirical Skill Certification & Cryptographic Audit Hash-Chain Integrity
    # =========================================================================
    # 1. Certify participating employees' skills based on verified delivery
    for skill_name in ["python", "software_development", "git"]:
        await employee_repo.verify_skill(dev_emp.employee_id, skill_name, task_evidence_id=task_id)
    for skill_name in ["test_automation", "qa_verification"]:
        await employee_repo.verify_skill(qa_emp.employee_id, skill_name, task_evidence_id=task_id)
    for skill_name in ["security_audit", "vulnerability_assessment"]:
        await employee_repo.verify_skill(sec_emp.employee_id, skill_name, task_evidence_id=task_id)

    # Reload employees and confirm skills certified
    dev_after = await employee_repo.get_by_employee_id(dev_emp.employee_id)
    qa_after = await employee_repo.get_by_employee_id(qa_emp.employee_id)
    sec_after = await employee_repo.get_by_employee_id(sec_emp.employee_id)

    assert all(s.verified for s in dev_after.skills if s.skill_name in ["python", "software_development", "git"])
    assert all(s.verified for s in qa_after.skills if s.skill_name in ["test_automation", "qa_verification"])
    assert all(s.verified for s in sec_after.skills if s.skill_name in ["security_audit", "vulnerability_assessment"])

    # 2. Immutable hash-chain verification over all Phase 8 audit records
    records = await audit_repo.list_all(limit=200)
    assert len(records) >= 8, f"Step 10 Failed: Expected >= 8 audit records, got {len(records)}"

    chain_valid, issues = await audit_repo.verify_chain_integrity()
    assert chain_valid is True, f"Step 10 Failed: Cryptographic audit log hash chain validation failed: {issues}"
    assert len(issues) == 0, f"Step 10 Failed: Audit chain issues detected: {issues}"

    ordered = AuditLogChainVerifier.order_chain(records)
    assert len(ordered) == len(records), "Step 10 Failed: Audit record count mismatch"
    last_record = ordered[-1]
    assert last_record.signature is not None and len(last_record.signature) == 64, (
        "Step 10 Failed: Invalid SHA-256 terminal chain hash"
    )
