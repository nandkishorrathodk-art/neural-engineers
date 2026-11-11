from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from neural.constants import DestructiveOperationType
from neural.db.repositories import (
    ApprovalRepository,
    AuditRepository,
)
from neural.evolution.candidate_staging import SelfEvolutionCandidateManager
from neural.evolution.models import (
    PROHIBITED_SELF_EVOLUTION_PATHS,
    PromotionGateError,
    ProposalStatus,
    ProtectedPathViolationError,
    SelfEvolutionProposal,
    is_protected_path,
)
from neural.evolution.multi_gate_verifier import (
    ASTSecurityScanner,
    SelfEvolutionMultiGateVerifier,
)
from neural.evolution.promotion_manager import SelfEvolutionPromotionManager
from neural.evolution.proposal_engine import EvidenceBasedProposalEngine
from neural.evolution.rollback_manager import (
    PostPromotionHealthMonitor,
    SelfEvolutionRollbackManager,
)
from neural.exceptions import HumanApprovalRequiredError
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
    repo = tmp_path / "test_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "ANCOS Master Repo")
    _git(repo, "config", "user.email", "master@ancos.corp")
    (repo / "README.md").write_text("# ANCOS Master Repo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial baseline commit")
    return repo


@pytest.fixture(autouse=True)
def reset_system_safety():
    reset_kill_switch()
    yield
    reset_kill_switch()


def test_protected_path_invariant_checks():
    """Validates that protected core policy, approval, and emergency files are strictly identified."""
    for path in PROHIBITED_SELF_EVOLUTION_PATHS:
        assert is_protected_path(path) is True, f"Path {path} must be protected"
        assert is_protected_path(path.replace("/", "\\")) is True

    # Traversal and substring tests
    assert is_protected_path("services/core/neural/security/approval_gates.py") is True
    assert is_protected_path("neural/security/kill_switch.py") is True
    assert is_protected_path("../etc/passwd") is True
    assert is_protected_path("services/core/neural/observability/audit.py") is True
    assert is_protected_path("services/core/neural/math/matrix_ops.py") is False


def test_evidence_based_proposal_creation_and_protected_path_defense():
    """Validates proposal generation and fail-closed rejection of proposals targeting protected paths."""
    engine = EvidenceBasedProposalEngine()

    # 1. Valid proposal creation
    proposal = engine.create_proposal(
        title="Vectorized Fast Path Matrix Operations",
        description="Replace iterative loops with vectorized math operations",
        category="performance",
        target_files=["services/core/neural/math/matrix_ops.py"],
        root_cause_rationale="O(N^3) naive iteration causes latency bottleneck.",
        baseline_metrics={"execution_time_ms": 120.0, "error_rate": 0.04},
        target_goals={"execution_time_ms": 30.0, "min_speedup_ratio": 3.0, "error_rate": 0.0},
        proposer_id="ANC-EMP-ENG-LEAD",
    )
    assert proposal.proposal_id.startswith("PROP-EVO-")
    assert proposal.status == ProposalStatus.DRAFT
    assert proposal.verify_signature() is True

    # 2. Rejection of proposal targeting protected path
    with pytest.raises(ProtectedPathViolationError, match="strictly prohibited"):
        engine.create_proposal(
            title="Bypass Approval Gates",
            description="Attempt to modify approval gates",
            category="optimization",
            target_files=["services/core/neural/security/approval_gates.py"],
            root_cause_rationale="Unauthorized modification",
            baseline_metrics={},
            target_goals={},
        )


def test_isolated_candidate_staging_lifecycle(tmp_path: Path):
    """Validates isolated candidate staging worktree creation and protected path enforcement."""
    repo = _setup_git_repository(tmp_path)
    managed_root = tmp_path / "data" / "evolution_worktrees"
    candidate_mgr = SelfEvolutionCandidateManager(repo, managed_root)

    proposal_id = "PROP-EVO-TEST-01"
    staging_receipt = candidate_mgr.provision_candidate_staging(proposal_id)
    assert staging_receipt.proposal_id == proposal_id
    assert staging_receipt.branch == f"self_evolution/{proposal_id}"
    assert staging_receipt.path.is_dir()

    # Protected core file modification attempt must fail closed immediately
    with pytest.raises(ProtectedPathViolationError, match="strictly prohibited"):
        candidate_mgr.apply_candidate_changes(
            proposal_id=proposal_id,
            files={"services/core/neural/security/kill_switch.py": "# hacked"},
            commit_message="Tamper kill switch",
        )

    # Valid modification applies cleanly in candidate worktree
    candidate_code = (
        "def compute_fast_dot_product(a: list[float], b: list[float]) -> float:\n"
        "    return sum(x * y for x, y in zip(a, b))\n"
    )
    test_code = (
        "from math_kernel import compute_fast_dot_product\n\n"
        "def test_dot_product():\n"
        "    assert compute_fast_dot_product([1.0, 2.0], [3.0, 4.0]) == 11.0\n"
    )
    commit_sha = candidate_mgr.apply_candidate_changes(
        proposal_id=proposal_id,
        files={"math_kernel.py": candidate_code, "test_math_kernel.py": test_code},
        commit_message="Implement fast dot product",
    )
    assert commit_sha != staging_receipt.base_commit
    assert (staging_receipt.path / "math_kernel.py").is_file()

    # Source repo main branch remains completely pristine
    assert not (repo / "math_kernel.py").exists()
    assert _git(repo, "rev-parse", "HEAD") == staging_receipt.base_commit


def test_ast_security_scanner_rules():
    """Validates AST security scanner detection of forbidden builtins and hardcoded secrets."""
    # 1. Clean code
    clean_code = "def add(a: int, b: int) -> int:\n    return a + b\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("clean.py", clean_code)
    assert passed is True
    assert score >= 0.99
    assert len(vulns) == 0

    # 2. Forbidden eval
    eval_code = "def run(x):\n    return eval(x)\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("eval.py", eval_code)
    assert passed is False
    assert any(v["type"] == "forbidden_builtin_call" for v in vulns)

    # 3. Dangerous os.system
    os_code = "import os\ndef run_cmd(c):\n    os.system(c)\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("os.py", os_code)
    assert passed is False
    assert any(v["type"] == "dangerous_os_call" for v in vulns)

    # 4. Hardcoded secret
    secret_code = "api_key = 'abcdef1234567890abcdef1234567890'\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("secret.py", secret_code)
    assert passed is False
    assert any(v["type"] == "hardcoded_secret" for v in vulns)


def test_multi_gate_verifier_execution(tmp_path: Path):
    """Validates multi-gate verification: regression, static analysis, AST security, benchmark."""
    repo = _setup_git_repository(tmp_path)
    managed_root = tmp_path / "data" / "evolution_worktrees"
    candidate_mgr = SelfEvolutionCandidateManager(repo, managed_root)

    proposal_id = "PROP-EVO-BENCH-01"
    candidate_mgr.provision_candidate_staging(proposal_id)

    math_code = (
        "def fast_vector_add(a: list[float], b: list[float]) -> list[float]:\n"
        "    return [x + y for x, y in zip(a, b)]\n"
    )
    test_code = (
        "from math_kernel import fast_vector_add\n\n"
        "def test_vector_add():\n"
        "    assert fast_vector_add([1.0, 2.0], [3.0, 4.0]) == [4.0, 6.0]\n"
    )
    candidate_mgr.apply_candidate_changes(
        proposal_id=proposal_id,
        files={"math_kernel.py": math_code, "test_math_kernel.py": test_code},
        commit_message="Vector add optimization",
    )

    proposal = SelfEvolutionProposal(
        proposal_id=proposal_id,
        title="Vector Add Optimization",
        description="Fast vector math",
        category="performance",
        target_files=["math_kernel.py"],
        root_cause_rationale="Redundant allocation",
        baseline_metrics={"execution_time_ms": 100.0, "error_rate": 0.05, "benchmark_throughput_ops_sec": 500.0},
        target_goals={"execution_time_ms": 25.0, "min_speedup_ratio": 3.0, "error_rate": 0.0},
    )

    verifier = SelfEvolutionMultiGateVerifier(candidate_mgr)

    # Candidate metrics satisfying benchmark target
    candidate_metrics = {
        "execution_time_ms": 20.0,
        "error_rate": 0.0,
        "benchmark_throughput_ops_sec": 2200.0,
    }
    receipt = verifier.verify_candidate(
        proposal=proposal,
        test_command=["python", "-m", "pytest", "-v"],
        candidate_benchmark_metrics=candidate_metrics,
    )

    assert receipt.passed is True
    assert receipt.regression_passed is True
    assert receipt.static_analysis_passed is True
    assert receipt.ast_security_passed is True
    assert receipt.benchmark_passed is True
    assert receipt.benchmark_comparison["speedup_ratio"] == 5.0
    assert receipt.verifier_id == "IndependentDeliverableVerifier"
    assert receipt.verify_signature() is True


@pytest.mark.asyncio
async def test_approval_gated_promotion_and_rollback_lifecycle(
    async_session: AsyncSession,
    tmp_path: Path,
):
    """
    Validates:
    1. Legacy auto_promote=True reports 'approval_required' without changing code.
    2. Promotion fails closed without valid approved HumanApprovalGate token.
    3. Promotion merges candidate, pins pre-evolution commit, and creates signed promotion receipt.
    4. Post-promotion health monitoring detects synthetic regression.
    5. Automated rollback restores pristine pre-evolution commit state and issues rollback receipt.
    """
    repo = _setup_git_repository(tmp_path)
    managed_root = tmp_path / "data" / "evolution_worktrees"
    candidate_mgr = SelfEvolutionCandidateManager(repo, managed_root)

    proposal_id = "PROP-EVO-PROMO-01"
    candidate_mgr.provision_candidate_staging(proposal_id)

    math_code = (
        "def compute_variance(data: list[float]) -> float:\n"
        "    mean = sum(data) / len(data)\n"
        "    return sum((x - mean) ** 2 for x in data) / len(data)\n"
    )
    test_code = (
        "import pytest\n"
        "from stats_kernel import compute_variance\n\n"
        "def test_variance():\n"
        "    assert compute_variance([1.0, 2.0, 3.0]) == pytest.approx(0.6666666666666666)\n"
    )
    candidate_mgr.apply_candidate_changes(
        proposal_id=proposal_id,
        files={"stats_kernel.py": math_code, "test_stats_kernel.py": test_code},
        commit_message="Implement variance calculation",
    )

    proposal = SelfEvolutionProposal(
        proposal_id=proposal_id,
        title="Optimized Variance Kernel",
        description="Vectorized variance formula",
        category="performance",
        target_files=["stats_kernel.py"],
        root_cause_rationale="Inefficient multi-pass variance",
        baseline_metrics={"execution_time_ms": 80.0, "error_rate": 0.02},
        target_goals={"execution_time_ms": 20.0, "min_speedup_ratio": 2.0, "error_rate": 0.0},
    )

    verifier = SelfEvolutionMultiGateVerifier(candidate_mgr)
    verif_receipt = verifier.verify_candidate(
        proposal=proposal,
        test_command=["python", "-m", "pytest", "-v"],
        candidate_benchmark_metrics={"execution_time_ms": 18.0, "error_rate": 0.0},
    )
    assert verif_receipt.passed is True

    promotion_mgr = SelfEvolutionPromotionManager(candidate_mgr)

    # 1. auto_promote=True is strictly blocked and reports approval_required
    readiness = promotion_mgr.assess_promotion_readiness(
        proposal=proposal,
        verification_receipt=verif_receipt,
        auto_promote=True,
    )
    assert readiness["eligible_for_promotion"] is True
    assert readiness["activation_status"] == "approval_required"
    assert readiness["auto_promote_blocked"] is True

    # 2. Promotion without approval token fails closed
    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    with pytest.raises(HumanApprovalRequiredError):
        await promotion_mgr.execute_promotion(
            proposal_id=proposal_id,
            target_branch="main",
            verification_receipt=verif_receipt,
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )

    # Pre-evolution commit before promotion
    pre_evolution_commit = _git(repo, "rev-parse", "HEAD")

    # 3. Create and approve human approval token
    approval = await approval_repo.create(
        action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
        description=f"Approve self-evolution candidate promotion for {proposal_id}",
        justification="Verified by IndependentDeliverableVerifier with 4.4x speedup",
        requested_by="ANC-EMP-LEAD-01",
        details={"proposal_id": proposal_id, "verification_receipt_id": verif_receipt.receipt_id},
    )
    await approval_repo.decide(approval.id, decision="approved", decided_by="owner@ancos.corp")

    # 4. Execute approved promotion into production
    prom_receipt = await promotion_mgr.execute_promotion(
        proposal_id=proposal_id,
        target_branch="main",
        verification_receipt=verif_receipt,
        approval_id=approval.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
        requested_by="ANC-EMP-LEAD-01",
    )
    assert prom_receipt.pre_evolution_commit == pre_evolution_commit.lower()
    assert prom_receipt.promoted_commit != pre_evolution_commit.lower()
    assert prom_receipt.verify_signature() is True
    assert (repo / "stats_kernel.py").is_file()

    # 5. Inject synthetic regression into production
    buggy_code = "def compute_variance(data):\n    raise RuntimeError('SYNTHETIC_REGRESSION_CRASH')\n"
    (repo / "stats_kernel.py").write_text(buggy_code, encoding="utf-8")
    _git(repo, "add", "stats_kernel.py")
    _git(repo, "commit", "-m", "Injected synthetic regression")
    bad_commit = _git(repo, "rev-parse", "HEAD")

    # 6. Post-promotion health monitor detects regression
    monitor = PostPromotionHealthMonitor(repo)
    health_report = monitor.check_health(
        canary_command=["python", "-m", "pytest", "-v"],
    )
    assert health_report.healthy is False
    assert health_report.anomaly_detected is not None
    assert health_report.anomaly_detected["type"] == "POST_PROMOTION_REGRESSION_DETECTED"

    # 7. Automated fail-closed rollback restores pinned pre-evolution commit state
    rollback_mgr = SelfEvolutionRollbackManager(repo)
    rb_receipt = await rollback_mgr.execute_rollback(
        proposal_id=proposal_id,
        target_branch="main",
        pre_evolution_commit=pre_evolution_commit,
        anomaly_detected=health_report.anomaly_detected,
        audit_repo=audit_repo,
    )
    assert rb_receipt.rolled_back_commit == bad_commit.lower()
    assert rb_receipt.restored_commit == pre_evolution_commit.lower()
    assert rb_receipt.verify_signature() is True

    # Verify audit record was written by rollback manager
    audit_entries = await audit_repo.list_all(limit=10)
    assert any(e.action == "ROLLBACK_RESTORED_PRISTINE_STATE" for e in audit_entries)

    # Confirm repository HEAD is restored to pristine pre-evolution commit state
    restored_head = _git(repo, "rev-parse", "HEAD")
    assert restored_head == pre_evolution_commit
    assert not (repo / "stats_kernel.py").exists()

    # Post-rollback health is pristine
    post_rb_health = monitor.check_health()
    assert post_rb_health.healthy is True


def test_ast_security_scanner_attribute_and_builtin_evasions():
    """Validates that ASTSecurityScanner detects evasive attribute calls and dangerous OS functions."""
    # 1. builtins.eval evasion attempt
    b_eval = "import builtins\ndef run(x):\n    return builtins.eval(x)\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("b_eval.py", b_eval)
    assert passed is False
    assert any(v["type"] == "forbidden_builtin_call" for v in vulns)

    # 2. builtins.exec evasion attempt
    b_exec = "import builtins\ndef run(x):\n    builtins.exec(x)\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("b_exec.py", b_exec)
    assert passed is False
    assert any(v["type"] == "forbidden_builtin_call" for v in vulns)

    # 3. os.execl evasion attempt
    os_execl = "import os\ndef run():\n    os.execl('/bin/sh', 'sh')\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("os_execl.py", os_execl)
    assert passed is False
    assert any(v["type"] == "dangerous_os_call" for v in vulns)

    # 4. os.spawnl evasion attempt
    os_spawnl = "import os\ndef run():\n    os.spawnl(os.P_WAIT, '/bin/sh')\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("os_spawnl.py", os_spawnl)
    assert passed is False
    assert any(v["type"] == "dangerous_os_call" for v in vulns)

    # 5. pty.spawn evasion attempt
    pty_spawn = "import pty\ndef run():\n    pty.spawn('/bin/bash')\n"
    passed, score, vulns = ASTSecurityScanner.scan_code("pty_spawn.py", pty_spawn)
    assert passed is False
    assert any(v["type"] == "dangerous_os_call" for v in vulns)


def test_protected_path_invariants_extended():
    """Validates protection of broker_api, gui_action_policy, and relative variations."""
    assert is_protected_path("services/core/neural/containers/broker_api.py") is True
    assert is_protected_path("neural/containers/broker_api.py") is True
    assert is_protected_path("broker_api.py") is True
    assert is_protected_path("services/core/neural/gui_intelligence/safety/gui_action_policy.py") is True
    assert is_protected_path("gui_action_policy.py") is True
    assert is_protected_path("./services/core/neural/containers/broker_api.py") is True
    assert is_protected_path("services\\core\\neural\\containers\\broker_api.py") is True


@pytest.mark.asyncio
async def test_promotion_anti_drift_defense(async_session: AsyncSession, tmp_path: Path):
    """
    Validates anti-drift defense: if an unverified commit is added to the candidate branch
    after verification receipt is generated, promotion fails closed immediately.
    """
    repo = _setup_git_repository(tmp_path)
    managed_root = tmp_path / "data" / "evolution_worktrees"
    candidate_mgr = SelfEvolutionCandidateManager(repo, managed_root)

    proposal_id = "PROP-EVO-DRIFT-01"
    candidate_mgr.provision_candidate_staging(proposal_id)

    # Step 1: Initial verified code
    candidate_mgr.apply_candidate_changes(
        proposal_id=proposal_id,
        files={"verified_kernel.py": "def add(a, b): return a + b\n"},
        commit_message="Initial verified commit",
    )

    proposal = SelfEvolutionProposal(
        proposal_id=proposal_id,
        title="Verified Kernel",
        description="Math kernel",
        category="performance",
        target_files=["verified_kernel.py"],
        root_cause_rationale="Baseline",
        baseline_metrics={"latency_ms": 50.0},
        target_goals={"latency_ms": 10.0, "min_speedup_ratio": 2.0},
    )

    verifier = SelfEvolutionMultiGateVerifier(candidate_mgr)
    verif_receipt = verifier.verify_candidate(
        proposal=proposal,
        test_command=["python", "-c", "import sys; sys.exit(0)"],
        candidate_benchmark_metrics={"latency_ms": 8.0},
    )
    assert verif_receipt.passed is True

    # Step 2: Post-verification drift - someone commits an unverified modification
    candidate_mgr.apply_candidate_changes(
        proposal_id=proposal_id,
        files={"verified_kernel.py": "def add(a, b): return a + b + 999\n"},
        commit_message="Unverified rogue commit after verification",
    )

    # Step 3: Attempt promotion with valid human approval token
    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    approval = await approval_repo.create(
        action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
        description="Approve drift proposal",
        justification="Legitimate test",
        requested_by="ANC-EMP-TEST",
        details={"proposal_id": proposal_id, "verification_receipt_id": verif_receipt.receipt_id},
    )
    await approval_repo.decide(approval.id, decision="approved", decided_by="owner@ancos.corp")

    promotion_mgr = SelfEvolutionPromotionManager(candidate_mgr)
    with pytest.raises(PromotionGateError, match="Unverified commits were added|HEAD.*does not match"):
        await promotion_mgr.execute_promotion(
            proposal_id=proposal_id,
            target_branch="main",
            verification_receipt=verif_receipt,
            approval_id=approval.id,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )


@pytest.mark.asyncio
async def test_promotion_verification_receipt_mismatch_defense(async_session: AsyncSession, tmp_path: Path):
    """
    Validates that if approval token details specify a different verification receipt ID,
    promotion strictly fails closed.
    """
    repo = _setup_git_repository(tmp_path)
    managed_root = tmp_path / "data" / "evolution_worktrees"
    candidate_mgr = SelfEvolutionCandidateManager(repo, managed_root)

    proposal_id = "PROP-EVO-MISMATCH-01"
    candidate_mgr.provision_candidate_staging(proposal_id)

    candidate_mgr.apply_candidate_changes(
        proposal_id=proposal_id,
        files={"mismatch_kernel.py": "def mul(a, b): return a * b\n"},
        commit_message="Initial commit",
    )

    proposal = SelfEvolutionProposal(
        proposal_id=proposal_id,
        title="Mismatch Kernel",
        description="Math kernel",
        category="performance",
        target_files=["mismatch_kernel.py"],
        root_cause_rationale="Baseline",
        baseline_metrics={"latency_ms": 50.0},
        target_goals={"latency_ms": 10.0, "min_speedup_ratio": 2.0},
    )

    verifier = SelfEvolutionMultiGateVerifier(candidate_mgr)
    verif_receipt = verifier.verify_candidate(
        proposal=proposal,
        test_command=["python", "-c", "import sys; sys.exit(0)"],
        candidate_benchmark_metrics={"latency_ms": 8.0},
    )

    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    # Issue approval for a different receipt ID
    approval = await approval_repo.create(
        action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
        description="Approve mismatch proposal",
        justification="Legitimate test",
        requested_by="ANC-EMP-TEST",
        details={"proposal_id": proposal_id, "verification_receipt_id": "EVO-VERIF-DIFFERENT123"},
    )
    await approval_repo.decide(approval.id, decision="approved", decided_by="owner@ancos.corp")

    promotion_mgr = SelfEvolutionPromotionManager(candidate_mgr)
    with pytest.raises(PromotionGateError, match="granted for verification receipt"):
        await promotion_mgr.execute_promotion(
            proposal_id=proposal_id,
            target_branch="main",
            verification_receipt=verif_receipt,
            approval_id=approval.id,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )


def test_scope_boundary_enforcement(tmp_path: Path):
    """Validates that candidate changes modifying unauthorized files outside proposal targets fail verification."""
    repo = _setup_git_repository(tmp_path)
    managed_root = tmp_path / "data" / "evolution_worktrees"
    candidate_mgr = SelfEvolutionCandidateManager(repo, managed_root)

    proposal_id = "PROP-EVO-SCOPE-01"
    candidate_mgr.provision_candidate_staging(proposal_id)

    # Apply changes modifying an unauthorized file not in target_files
    candidate_mgr.apply_candidate_changes(
        proposal_id=proposal_id,
        files={"unauthorized_module.py": "def rogue(): pass\n"},
        commit_message="Unauthorized modification",
    )

    proposal = SelfEvolutionProposal(
        proposal_id=proposal_id,
        title="Authorized Scope",
        description="Testing scope bounds",
        category="performance",
        target_files=["authorized_math_kernel.py"],
        root_cause_rationale="Baseline",
        baseline_metrics={"latency_ms": 50.0},
        target_goals={"latency_ms": 10.0, "min_speedup_ratio": 2.0},
    )

    verifier = SelfEvolutionMultiGateVerifier(candidate_mgr)
    receipt = verifier.verify_candidate(
        proposal=proposal,
        test_command=["python", "-c", "import sys; sys.exit(0)"],
        candidate_benchmark_metrics={"latency_ms": 8.0},
    )
    assert receipt.passed is False
    assert "Scope Boundary Gate Failed" in receipt.notes


def test_candidate_worktree_cleanup_branch_deletion(tmp_path: Path):
    """Validates that worktree cleanup also deletes the branch and allows clean re-provisioning."""
    repo = _setup_git_repository(tmp_path)
    managed_root = tmp_path / "data" / "evolution_worktrees"
    candidate_mgr = SelfEvolutionCandidateManager(repo, managed_root)

    proposal_id = "PROP-EVO-CLEANUP-01"
    receipt = candidate_mgr.provision_candidate_staging(proposal_id)
    branch = receipt.branch

    # Verify branch exists in git repo
    branches_before = _git(repo, "branch", "--list")
    assert branch in branches_before

    # Clean up candidate worktree
    candidate_mgr.cleanup_candidate_worktree(proposal_id, delete_branch=True)

    # Verify branch was deleted
    branches_after = _git(repo, "branch", "--list")
    assert branch not in branches_after

    # Re-provisioning with the same proposal_id succeeds without branch collision
    new_receipt = candidate_mgr.provision_candidate_staging(proposal_id)
    assert new_receipt.branch == branch
