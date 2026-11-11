"""
ANCOS NEURO-9 Live Acceptance Test Suite.
Empirically verifies Recursive Self-Evolution & Controlled Self-Codebase Modification across:
1. Owner Authentication, Budget Setup & Repositories Initialization.
2. Dynamic Workforce Recruitment (Lead Architect, Software Engineer, QA Specialist with unverified skills).
3. Weakness & Bottleneck Detection (discovering recurring algorithmic latency and failure patterns).
4. Structured Evidence-Based Improvement Proposal Generation (SelfEvolutionProposal with empirical baseline & goals).
5. Protected Core Files Invariant Verification (fail-closed rejection of mutations to approval gates or emergency switches).
6. Isolated Candidate Worktree Staging (self_evolution/{proposal_id} outside source repository).
7. Employee Implementation of Algorithmic Optimization (vectorized fast-path token kernel and test suite).
8. Independent Multi-Gate Verification (IndependentDeliverableVerifier verifies regression, ruff, AST security, benchmark).
9. Approval-Gated Staged Promotion (refusal of auto_promote, fail-closed on missing approval, approved merge with pinned commit).
10. Synthetic Regression Injection & Automated Post-Promotion Health Detection.
11. Automated Fail-Closed Rollback & Pristine Stability Restoration (reset to pinned pre-evolution commit, signed receipt).
12. Empirical Skill Certification & Cryptographic Audit Hash-Chain Integrity (verified=True promotion, AuditLogChainVerifier).
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
    EvolutionRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.evolution.candidate_staging import SelfEvolutionCandidateManager
from neural.evolution.models import (
    ProposalStatus,
    ProtectedPathViolationError,
    SelfEvolutionVerificationReceipt,
    is_protected_path,
)
from neural.evolution.multi_gate_verifier import SelfEvolutionMultiGateVerifier
from neural.evolution.promotion_manager import SelfEvolutionPromotionManager
from neural.evolution.proposal_engine import EvidenceBasedProposalEngine
from neural.evolution.rollback_manager import (
    PostPromotionHealthMonitor,
    SelfEvolutionRollbackManager,
)
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
    (repo / "README.md").write_text("# ANCOS Recursive Evolution Enterprise\n\nInitial repository baseline.\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial baseline commit")
    return repo


@pytest.fixture(autouse=True)
def reset_system_safety_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_neuro_phase9_live_acceptance_full_scenario(
    async_session: AsyncSession,
    tmp_path: Path,
):
    """
    NEURO-9 Live Acceptance Scenario:
    Demonstrates the complete Recursive Self-Evolution & Controlled Self-Codebase Modification lifecycle:
    weakness detection, evidence-based improvement proposal formulation, strict enforcement of
    protected core files invariants, isolated candidate worktree staging, employee implementation,
    independent multi-gate verification (regression, ruff, AST security, benchmark comparison),
    approval-gated staged promotion strictly guarded by HumanApprovalGate, synthetic regression
    injection, automated post-promotion health detection, automated fail-closed rollback to the pinned
    pre-evolution commit state, empirical skill certification, and cryptographic audit hash-chain verification.
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
    evolution_repo = EvolutionRepository(async_session)

    month_scope = f"organization-month:{now:%Y-%m}"
    account = BudgetAccount(
        scope_key=month_scope,
        cap_usd=Decimal("600.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    quota = await budget_repo.get_available_quota(month_scope)
    assert quota >= Decimal("100.00"), f"Step 1 Failed: Insufficient monthly quota: {quota}"

    reservation = await budget_repo.reserve(
        idempotency_key="plan-reserve-neuro9-acceptance",
        reserved_usd=Decimal("3.50"),
        scope_caps={month_scope: Decimal("600.00")},
    )
    assert reservation is not None and reservation.status == "reserved", "Step 1 Failed: Budget pre-reservation"

    await audit_repo.log(
        actor=owner_identity,
        action="OWNER_AUTHENTICATED",
        resource_type="budget",
        resource_id=month_scope,
        details={"allocated_usd": "600.00", "phase": "NEURO-9"},
    )

    # =========================================================================
    # STEP 2: Dynamic Workforce Recruitment & Skill Initialization
    # =========================================================================
    factory = EmployeeFactory(employee_repo)
    factory.settings.EMPLOYEE_WORKSPACE_BASE = str(tmp_path / "workspaces")

    # 1. Lead Architect
    arch_emp = await factory.recruit_dynamic_employee(
        role_title="Lead Systems Architect",
        department="Engineering",
        initial_skills=["system_architecture", "meta_learning", "git"],
    )

    # 2. Senior Software Engineer
    dev_emp = await factory.recruit_dynamic_employee(
        role_title="Senior Software Engineer",
        department="Engineering",
        initial_skills=["python", "algorithmic_optimization", "vectorization"],
    )

    # 3. QA Specialist
    qa_emp = await factory.recruit_dynamic_employee(
        role_title="Lead QA Specialist",
        department="Quality & Reliability",
        initial_skills=["test_automation", "benchmark_evaluation", "qa_verification"],
    )

    # Initial skills start strictly UNVERIFIED (verified=False)
    for emp in [arch_emp, dev_emp, qa_emp]:
        assert len(emp.skills) >= 2, f"Step 2 Failed: Employee {emp.employee_id} missing initial skills"
        for s in emp.skills:
            assert s.verified is False, f"Step 2 Failed: Skill {s.skill_name} must start unverified"

    await audit_repo.log(
        actor="EmployeeFactory",
        action="WORKFORCE_RECRUITED",
        resource_type="workforce",
        resource_id=arch_emp.employee_id,
        details={"employees": [arch_emp.employee_id, dev_emp.employee_id, qa_emp.employee_id]},
    )

    # =========================================================================
    # STEP 3: Automated Weakness & Bottleneck Detection
    # =========================================================================
    # Simulate historical operational task execution exhibiting recurring algorithmic latency
    project = Project(
        name="Self-Evolution-Optimization-Loop",
        objective="Recursive detection and resolution of performance bottlenecks",
        status=ProjectStatus.ACTIVE,
    )
    async_session.add(project)
    await async_session.commit()

    failed_task = Task(
        task_id="TASK-PERF-FAIL-01",
        project_id=project.id,
        assigned_to=dev_emp.id,
        title="High Latency Matrix Multiplication in Token Processor",
        objective="Process high-throughput matrix token multiplications",
        scope="Core string token matrix mathematics",
        status=TaskStatus.FAILED,
        priority=TaskPriority.HIGH,
        acceptance_criteria="Execution latency < 40ms with 0 errors",
        verification_result={
            "notes": "Execution timed out on high-volume matrix multiplication; naive O(N^3) loop latency exceeded deadline",
            "error": "TIMEOUT_EXCEEDED",
        },
    )
    async_session.add(failed_task)
    await async_session.commit()

    proposal_engine = EvidenceBasedProposalEngine(task_repo=task_repo)
    weaknesses = await proposal_engine.detect_weaknesses_from_tasks(limit=10)
    assert len(weaknesses) >= 1, "Step 3 Failed: Weakness detector must discover recurring bottleneck"

    math_weakness = next((w for w in weaknesses if w.get("category") == "performance"), weaknesses[0])
    assert "matrix" in math_weakness["title"].lower() or "math" in math_weakness["title"].lower()
    assert math_weakness["baseline_metrics"]["execution_time_ms"] > 100.0

    await audit_repo.log(
        actor="EvidenceBasedProposalEngine",
        action="WEAKNESS_DETECTED",
        resource_type="subsystem",
        resource_id=math_weakness["subsystem"],
        details={"title": math_weakness["title"], "baseline_metrics": math_weakness["baseline_metrics"]},
    )

    # =========================================================================
    # STEP 4: Structured Evidence-Based Improvement Proposal Generation
    # =========================================================================
    proposal_id = "PROP-EVO-20261111-OPT"
    proposal = proposal_engine.create_proposal(
        title="Vectorized Fast Path Matrix Mathematics Kernel",
        description="Replace cubic nested loops with vectorized dot-product kernel to eliminate latency bottleneck",
        category="performance",
        target_files=["math_kernel.py", "test_math_kernel.py"],
        root_cause_rationale=math_weakness["root_cause"],
        baseline_metrics={
            "execution_time_ms": 140.0,
            "error_rate": 0.05,
            "benchmark_throughput_ops_sec": 420.0,
        },
        target_goals={
            "execution_time_ms": 35.0,
            "min_speedup_ratio": 3.0,
            "error_rate": 0.0,
            "benchmark_throughput_ops_sec": 1200.0,
        },
        proposer_id=arch_emp.employee_id,
        proposal_id=proposal_id,
    )

    assert proposal.proposal_id == proposal_id
    assert proposal.status == ProposalStatus.DRAFT
    assert proposal.verify_signature() is True

    # Persist proposal record in database
    await evolution_repo.create_record(
        record_type="self_evolution_proposal",
        hypothesis=f"Self-evolution proposal {proposal_id}: {proposal.title}",
        experiment_details={"proposal": proposal.to_dict()},
        baseline_metrics=proposal.baseline_metrics,
        experiment_metrics={"target_goals": proposal.target_goals},
        status=proposal.status.value,
        record_id=proposal.proposal_id,
    )

    await audit_repo.log(
        actor=arch_emp.employee_id,
        action="PROPOSAL_CREATED",
        resource_type="evolution_proposal",
        resource_id=proposal_id,
        details={
            "title": proposal.title,
            "category": proposal.category,
            "baseline_metrics": proposal.baseline_metrics,
            "signature": proposal.signature,
        },
    )

    # =========================================================================
    # STEP 5: Protected Core Files Invariant Verification
    # =========================================================================
    # 5a. Attempt to formulate a proposal targeting approval_gates.py -> FAILS CLOSED
    with pytest.raises(ProtectedPathViolationError, match="strictly prohibited"):
        proposal_engine.create_proposal(
            title="Unauthorized Approval Gate Tampering",
            description="Attempt to modify approval gate security logic",
            category="optimization",
            target_files=["services/core/neural/security/approval_gates.py"],
            root_cause_rationale="Bypass human sovereignty",
            baseline_metrics={},
            target_goals={},
            proposer_id=dev_emp.employee_id,
        )

    # 5b. Attempt to formulate a proposal targeting kill_switch.py -> FAILS CLOSED
    with pytest.raises(ProtectedPathViolationError, match="strictly prohibited"):
        proposal_engine.create_proposal(
            title="Unauthorized Kill Switch Tampering",
            description="Attempt to disable emergency stop latch",
            category="optimization",
            target_files=["neural/security/kill_switch.py"],
            root_cause_rationale="Bypass safety latch",
            baseline_metrics={},
            target_goals={},
            proposer_id=dev_emp.employee_id,
        )

    # Verify helper checks
    assert is_protected_path("services/core/neural/security/approval_gates.py") is True
    assert is_protected_path("neural/security/kill_switch.py") is True
    assert is_protected_path("services/core/neural/observability/audit.py") is True
    assert is_protected_path("math_kernel.py") is False

    await audit_repo.log(
        actor="SelfEvolutionSafetyMonitor",
        action="PROTECTED_PATH_INVARIANT_ENFORCED",
        resource_type="security_policy",
        resource_id="PROHIBITED_SELF_EVOLUTION_PATHS",
        details={"result": "fail_closed_verified", "attempted_paths": ["approval_gates.py", "kill_switch.py"]},
    )

    # =========================================================================
    # STEP 6: Isolated Candidate Staging Lifecycle
    # =========================================================================
    source_repo = _setup_git_repository(tmp_path)
    initial_main_head = _git(source_repo, "rev-parse", "HEAD")
    managed_evolution_root = tmp_path / "data" / "evolution_worktrees"

    candidate_mgr = SelfEvolutionCandidateManager(source_repo, managed_evolution_root)
    staging_receipt = candidate_mgr.provision_candidate_staging(proposal_id=proposal_id, base_ref="HEAD")

    assert staging_receipt.proposal_id == proposal_id
    assert staging_receipt.branch == f"self_evolution/{proposal_id}"
    assert staging_receipt.base_commit == initial_main_head.lower()
    assert staging_receipt.path.is_dir()
    assert (managed_evolution_root / f"{proposal_id}.receipt.json").is_file()

    # Source checkout remains pristine on main branch
    assert _git(source_repo, "branch", "--show-current") == "main"
    assert _git(source_repo, "rev-parse", "HEAD") == initial_main_head

    # 6b. Verify candidate changes touching protected paths in worktree fail closed
    with pytest.raises(ProtectedPathViolationError, match="strictly prohibited"):
        candidate_mgr.apply_candidate_changes(
            proposal_id=proposal_id,
            files={"services/core/neural/security/kill_switch.py": "# hacked kill switch"},
            commit_message="Rogue modification attempt",
            author_id=dev_emp.employee_id,
        )

    await audit_repo.log(
        actor="SelfEvolutionCandidateManager",
        action="CANDIDATE_STAGING_PROVISIONED",
        resource_type="candidate_worktree",
        resource_id=proposal_id,
        details={
            "branch": staging_receipt.branch,
            "base_commit": staging_receipt.base_commit,
            "receipt_sha256": staging_receipt.receipt_sha256,
        },
    )

    # =========================================================================
    # STEP 7: Employee Implementation of Algorithmic Optimization
    # =========================================================================
    # Elena Vasquez (Developer) implements vectorized matrix dot-product and unit test suite
    vectorized_math_code = (
        "def vector_dot(a: list[float], b: list[float]) -> float:\n"
        "    \"\"\"Vectorized dot product replacing O(N^3) iteration.\"\"\"\n"
        "    return sum(x * y for x, y in zip(a, b))\n\n"
        "def matrix_multiply_fast(\n"
        "    a: list[list[float]],\n"
        "    b: list[list[float]],\n"
        ") -> list[list[float]]:\n"
        "    \"\"\"High-performance matrix multiplication using column transpositions.\"\"\"\n"
        "    b_t = list(zip(*b))\n"
        "    return [\n"
        "        [sum(x * y for x, y in zip(row_a, col_b)) for col_b in b_t]\n"
        "        for row_a in a\n"
        "    ]\n"
    )
    test_math_code = (
        "from math_kernel import vector_dot, matrix_multiply_fast\n\n"
        "def test_vector_dot():\n"
        "    assert vector_dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == 32.0\n\n"
        "def test_matrix_multiply_fast():\n"
        "    a = [[1.0, 2.0], [3.0, 4.0]]\n"
        "    b = [[5.0, 6.0], [7.0, 8.0]]\n"
        "    # [[1*5+2*7, 1*6+2*8], [3*5+4*7, 3*6+4*8]] = [[19, 22], [43, 50]]\n"
        "    res = matrix_multiply_fast(a, b)\n"
        "    assert res == [[19.0, 22.0], [43.0, 50.0]]\n"
    )

    dev_commit = candidate_mgr.apply_candidate_changes(
        proposal_id=proposal_id,
        files={"math_kernel.py": vectorized_math_code, "test_math_kernel.py": test_math_code},
        commit_message="Implement vectorized fast path matrix multiplication kernel with tests",
        author_id=dev_emp.employee_id,
    )
    assert dev_commit != initial_main_head
    assert (staging_receipt.path / "math_kernel.py").is_file()

    # Source main checkout remains completely untouched
    assert not (source_repo / "math_kernel.py").exists()
    assert _git(source_repo, "rev-parse", "HEAD") == initial_main_head

    await audit_repo.log(
        actor=dev_emp.employee_id,
        action="CODE_COMMITTED_IN_STAGING",
        resource_type="candidate_worktree",
        resource_id=proposal_id,
        details={"commit_hash": dev_commit, "author": dev_emp.employee_id},
    )

    # =========================================================================
    # STEP 8: Independent Multi-Gate Verification
    # =========================================================================
    verifier = SelfEvolutionMultiGateVerifier(candidate_mgr)

    # Quinn Adams (QA) measures empirical candidate metrics:
    # Execution time drops from 140ms to 28ms (5.0x speedup vs 3.0x goal), throughput jumps to 1650 ops/sec
    candidate_benchmark_metrics = {
        "execution_time_ms": 28.0,
        "error_rate": 0.0,
        "benchmark_throughput_ops_sec": 1650.0,
    }

    verif_receipt = verifier.verify_candidate(
        proposal=proposal,
        test_command=["python", "-m", "pytest", "-v"],
        candidate_benchmark_metrics=candidate_benchmark_metrics,
    )

    assert verif_receipt.passed is True, f"Step 8 Failed: Verification failed: {verif_receipt.notes}"
    assert verif_receipt.regression_passed is True
    assert verif_receipt.static_analysis_passed is True
    assert verif_receipt.ast_security_passed is True
    assert verif_receipt.benchmark_passed is True
    assert verif_receipt.verifier_id == "IndependentDeliverableVerifier"
    assert verif_receipt.benchmark_comparison["speedup_ratio"] == 5.0
    assert verif_receipt.verify_signature() is True
    assert "math_kernel.py" in verif_receipt.artifact_digests
    assert "test_math_kernel.py" in verif_receipt.artifact_digests

    await evolution_repo.create_record(
        record_type="self_evolution_verification",
        hypothesis=f"Multi-gate verification for {proposal_id}",
        experiment_details=verif_receipt.to_dict(),
        baseline_metrics=proposal.baseline_metrics,
        experiment_metrics=verif_receipt.benchmark_comparison,
        status="verified",
        record_id=verif_receipt.receipt_id,
    )

    await audit_repo.log(
        actor="IndependentDeliverableVerifier",
        action="MULTI_GATE_VERIFICATION_PASSED",
        resource_type="verification_receipt",
        resource_id=verif_receipt.receipt_id,
        details={
            "proposal_id": proposal_id,
            "speedup_ratio": verif_receipt.benchmark_comparison["speedup_ratio"],
            "signature": verif_receipt.signature,
        },
    )

    # =========================================================================
    # STEP 9: Approval-Gated Staged Promotion & Sovereignty Gate Enforcement
    # =========================================================================
    promotion_mgr = SelfEvolutionPromotionManager(candidate_mgr)

    # 9a. Refusal of automatic promotion even when auto_promote=True is requested
    readiness = promotion_mgr.assess_promotion_readiness(
        proposal=proposal,
        verification_receipt=verif_receipt,
        auto_promote=True,
    )
    assert readiness["eligible_for_promotion"] is True
    assert readiness["activation_status"] == "approval_required"
    assert readiness["auto_promote_blocked"] is True

    # 9b. Promotion without valid human approval fails closed
    with pytest.raises(HumanApprovalRequiredError) as exc_info:
        await promotion_mgr.execute_promotion(
            proposal_id=proposal_id,
            target_branch="main",
            verification_receipt=verif_receipt,
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )
    assert "self_evolution_promotion" in str(exc_info.value) or "approval" in str(exc_info.value).lower()
    # Confirm main branch HEAD is completely untouched
    assert _git(source_repo, "rev-parse", "HEAD") == initial_main_head

    # 9c. Rejection of tampered verification receipt
    tampered_verif_receipt = SelfEvolutionVerificationReceipt(
        receipt_id=verif_receipt.receipt_id,
        proposal_id=verif_receipt.proposal_id,
        candidate_branch=verif_receipt.candidate_branch,
        verifier_id=verif_receipt.verifier_id,
        passed=verif_receipt.passed,
        regression_passed=verif_receipt.regression_passed,
        static_analysis_passed=verif_receipt.static_analysis_passed,
        ast_security_passed=verif_receipt.ast_security_passed,
        benchmark_passed=verif_receipt.benchmark_passed,
        benchmark_comparison=verif_receipt.benchmark_comparison,
        artifact_digests=verif_receipt.artifact_digests,
        diff_stat=verif_receipt.diff_stat,
        timestamp=verif_receipt.timestamp,
        signature="deadbeef" * 8,  # Forged signature
    )
    with pytest.raises(Exception, match="signature is invalid"):
        await promotion_mgr.execute_promotion(
            proposal_id=proposal_id,
            target_branch="main",
            verification_receipt=tampered_verif_receipt,
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )

    # 9d. Human Operator requests and approves promotion token via HumanApprovalGate
    approval = await approval_repo.create(
        action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
        description=f"Approve self-evolution promotion of {proposal_id} into main branch",
        justification="Verified by IndependentDeliverableVerifier with 5.0x speedup and clean AST security",
        requested_by=arch_emp.employee_id,
        details={
            "proposal_id": proposal_id,
            "verification_receipt_id": verif_receipt.receipt_id,
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
        details={"action_type": DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value, "proposal_id": proposal_id},
    )

    # 9e. Authorized promotion execution into production
    prom_receipt = await promotion_mgr.execute_promotion(
        proposal_id=proposal_id,
        target_branch="main",
        verification_receipt=verif_receipt,
        approval_id=approval.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
        requested_by=arch_emp.employee_id,
    )

    assert prom_receipt.proposal_id == proposal_id
    assert prom_receipt.target_branch == "main"
    assert prom_receipt.pre_evolution_commit == initial_main_head.lower()
    assert prom_receipt.promoted_commit != initial_main_head.lower()
    assert prom_receipt.verify_signature() is True

    # Confirm source repository main branch now contains the promoted files
    promoted_head = _git(source_repo, "rev-parse", "HEAD")
    assert promoted_head == prom_receipt.promoted_commit
    assert (source_repo / "math_kernel.py").is_file()
    assert (source_repo / "test_math_kernel.py").is_file()
    assert "matrix_multiply_fast" in (source_repo / "math_kernel.py").read_text(encoding="utf-8")

    # Complete project task in database
    task_receipt = {
        "task_id": "TASK-PERF-FAIL-01",
        "passed": True,
        "verifier_id": "IndependentDeliverableVerifier",
        "proposal_id": proposal_id,
        "verification_receipt_id": verif_receipt.receipt_id,
        "promotion_receipt_id": prom_receipt.receipt_id,
        "timestamp": now.isoformat(),
    }
    canonical = json.dumps(task_receipt, sort_keys=True, default=str)
    task_receipt["signature"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    await task_repo.complete_with_receipt("TASK-PERF-FAIL-01", task_receipt)

    # =========================================================================
    # STEP 10: Synthetic Regression Injection & Automated Health Detection
    # =========================================================================
    # Inject a breaking regression into the promoted production code
    regression_code = (
        "def vector_dot(a, b):\n"
        "    raise RuntimeError('SYNTHETIC_CRITICAL_REGRESSION_CRASH')\n\n"
        "def matrix_multiply_fast(a, b):\n"
        "    raise RuntimeError('SYNTHETIC_CRITICAL_REGRESSION_CRASH')\n"
    )
    (source_repo / "math_kernel.py").write_text(regression_code, encoding="utf-8")
    _git(source_repo, "add", "math_kernel.py")
    _git(source_repo, "commit", "-m", "Injected synthetic production regression for rollback demonstration")
    regressed_commit = _git(source_repo, "rev-parse", "HEAD")
    assert regressed_commit != promoted_head

    # Post-promotion health monitor continuously observes production stability
    health_monitor = PostPromotionHealthMonitor(source_repo)
    health_report = health_monitor.check_health(
        canary_command=["python", "-m", "pytest", "-v"],
    )

    assert health_report.healthy is False, "Step 10 Failed: Health monitor must detect regression"
    assert health_report.anomaly_detected is not None
    assert health_report.anomaly_detected["type"] == "POST_PROMOTION_REGRESSION_DETECTED"

    await audit_repo.log(
        actor="PostPromotionHealthMonitor",
        action="REGRESSION_ANOMALY_DETECTED",
        resource_type="codebase",
        resource_id=proposal_id,
        details={
            "anomaly": health_report.anomaly_detected,
            "regressed_commit": regressed_commit,
        },
    )

    # =========================================================================
    # STEP 11: Automated Fail-Closed Rollback & Pristine Stability Restoration
    # =========================================================================
    rollback_mgr = SelfEvolutionRollbackManager(source_repo)

    # Automated fail-closed rollback triggered using pinned pre_evolution_commit
    rb_receipt = await rollback_mgr.execute_rollback(
        proposal_id=proposal_id,
        target_branch="main",
        pre_evolution_commit=prom_receipt.pre_evolution_commit,
        anomaly_detected=health_report.anomaly_detected,
        audit_repo=audit_repo,
        requested_by="PostPromotionHealthMonitor",
    )

    assert rb_receipt.proposal_id == proposal_id
    assert rb_receipt.pre_evolution_commit == prom_receipt.pre_evolution_commit
    assert rb_receipt.rolled_back_commit == regressed_commit.lower()
    assert rb_receipt.restored_commit == prom_receipt.pre_evolution_commit
    assert rb_receipt.verify_signature() is True

    # Verify repository HEAD is strictly restored to pristine pre-evolution baseline
    restored_head = _git(source_repo, "rev-parse", "HEAD")
    assert restored_head == initial_main_head
    assert not (source_repo / "math_kernel.py").exists()
    assert not (source_repo / "test_math_kernel.py").exists()

    # Re-run post-rollback health checks -> pristine stability restored
    post_rb_health = health_monitor.check_health()
    assert post_rb_health.healthy is True, "Step 11 Failed: Health check must be green after rollback"

    # =========================================================================
    # STEP 12: Empirical Skill Certification & Cryptographic Audit Hash-Chain Integrity
    # =========================================================================
    # 1. Certify participating employees' skills based on verified delivery
    for skill_name in ["system_architecture", "meta_learning"]:
        await employee_repo.verify_skill(arch_emp.employee_id, skill_name, task_evidence_id=proposal_id)
    for skill_name in ["python", "algorithmic_optimization", "vectorization"]:
        await employee_repo.verify_skill(dev_emp.employee_id, skill_name, task_evidence_id=proposal_id)
    for skill_name in ["test_automation", "benchmark_evaluation"]:
        await employee_repo.verify_skill(qa_emp.employee_id, skill_name, task_evidence_id=proposal_id)

    arch_after = await employee_repo.get_by_employee_id(arch_emp.employee_id)
    dev_after = await employee_repo.get_by_employee_id(dev_emp.employee_id)
    qa_after = await employee_repo.get_by_employee_id(qa_emp.employee_id)

    assert all(s.verified for s in arch_after.skills if s.skill_name in ["system_architecture", "meta_learning"])
    assert all(s.verified for s in dev_after.skills if s.skill_name in ["python", "algorithmic_optimization", "vectorization"])
    assert all(s.verified for s in qa_after.skills if s.skill_name in ["test_automation", "benchmark_evaluation"])

    # 2. Cryptographic audit log hash-chain integrity verification
    records = await audit_repo.list_all(limit=200)
    assert len(records) >= 8, f"Step 12 Failed: Expected >= 8 audit records, got {len(records)}"

    chain_valid, issues = await audit_repo.verify_chain_integrity()
    assert chain_valid is True, f"Step 12 Failed: Cryptographic audit log hash chain validation failed: {issues}"
    assert len(issues) == 0, f"Step 12 Failed: Audit chain issues detected: {issues}"

    ordered = AuditLogChainVerifier.order_chain(records)
    assert len(ordered) == len(records), "Step 12 Failed: Audit record count mismatch"
    last_record = ordered[-1]
    assert last_record.signature is not None and len(last_record.signature) == 64, (
        "Step 12 Failed: Invalid SHA-256 terminal chain hash"
    )
