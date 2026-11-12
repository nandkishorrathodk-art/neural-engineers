from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from neural.cli.self_evolution_cli import CLIAutonomousEvolutionRunner
from neural.evolution.candidate_staging import SelfEvolutionCandidateManager
from neural.evolution.models import (
    PROHIBITED_SELF_EVOLUTION_PATHS,
    SelfEvolutionProposal,
    is_protected_path,
)
from neural.evolution.multi_gate_verifier import SelfEvolutionMultiGateVerifier
from neural.evolution.proposal_engine import EvidenceBasedProposalEngine
from neural.evolution.rollback_manager import SelfEvolutionRollbackManager
from rich.console import Console


def _init_test_git_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "ANCOSCoder"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "coder@ancos.local"], cwd=repo_dir, check=True)
    readme = repo_dir / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True)
    return repo_dir


# -----------------------------------------------------------------------------
# 1. Rollback Manager Safety (No git clean -fdx on repo root)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rollback_preserves_env_db_and_venv_in_repo_root(tmp_path: Path):
    """
    Validates that rollback does NOT execute git clean -fdx on the main repo root,
    safely preserving untracked .env secrets, production sqlite databases, and .venv.
    """
    repo_dir = _init_test_git_repo(tmp_path / "repo")
    pre_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Simulate critical untracked assets in the repository root
    env_file = repo_dir / ".env"
    env_file.write_text("SECRET_API_KEY=super_secret_dont_delete\n", encoding="utf-8")

    db_dir = repo_dir / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "production.db"
    db_file.write_bytes(b"SQLITE_DATA_RECORD_BYTES")

    venv_dir = repo_dir / ".venv" / "Scripts"
    venv_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = repo_dir / ".venv" / "pyvenv.cfg"
    cfg_file.write_text("home = C:\\Python\n", encoding="utf-8")

    # Introduce a bad commit on main
    bad_file = repo_dir / "broken_feature.py"
    bad_file.write_text("raise RuntimeError('Fatal regression')\n", encoding="utf-8")
    subprocess.run(["git", "add", "broken_feature.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "Bad candidate commit"], cwd=repo_dir, check=True)

    # Provision candidate worktree staging
    managed_root = tmp_path / "worktrees"
    staging_mgr = SelfEvolutionCandidateManager(repo_dir, managed_root)
    proposal_id = "PROP-EVO-ROLLBACK-TEST"
    candidate_receipt = staging_mgr.provision_candidate_staging(proposal_id)
    assert candidate_receipt.path.exists()

    # Execute rollback
    rollback_mgr = SelfEvolutionRollbackManager(repo_dir)
    receipt = await rollback_mgr.execute_rollback(
        proposal_id=proposal_id,
        target_branch="master",
        pre_evolution_commit=pre_commit,
        anomaly_detected={"type": "REGRESSION_CRITICAL"},
        candidate_worktree_path=candidate_receipt.path,
    )

    assert receipt.restored_commit.lower() == pre_commit.lower()

    # CRITICAL INVARIANT: Untracked .env, database, and .venv MUST be preserved!
    assert env_file.is_file(), ".env was deleted by rollback!"
    assert env_file.read_text(encoding="utf-8") == "SECRET_API_KEY=super_secret_dont_delete\n"
    assert db_file.is_file(), "Database was wiped by rollback!"
    assert cfg_file.is_file(), ".venv was destroyed by rollback!"

    # Candidate worktree was safely removed
    assert not candidate_receipt.path.exists(), "Candidate worktree was not removed!"

    # Also verify alias rollback_proposal exists
    assert rollback_mgr.rollback_proposal == rollback_mgr.execute_rollback


# -----------------------------------------------------------------------------
# 2. Prohibited Self-Evolution Paths (Evolution & Governance Protection)
# -----------------------------------------------------------------------------
def test_prohibited_paths_includes_evolution_and_governance():
    """Validates that services/core/neural/evolution and governance are strictly prohibited."""
    assert "services/core/neural/evolution" in PROHIBITED_SELF_EVOLUTION_PATHS
    assert "services/core/neural/governance" in PROHIBITED_SELF_EVOLUTION_PATHS

    # Exact directory paths
    assert is_protected_path("services/core/neural/evolution") is True
    assert is_protected_path("services/core/neural/governance") is True

    # Files inside evolution and governance
    assert is_protected_path("services/core/neural/evolution/models.py") is True
    assert is_protected_path("services/core/neural/evolution/multi_gate_verifier.py") is True
    assert is_protected_path("services/core/neural/evolution/rollback_manager.py") is True
    assert is_protected_path("services/core/neural/governance/approval_policy.py") is True
    assert is_protected_path("neural/evolution/curriculum.py") is True
    assert is_protected_path("neural/governance/kill_switch.py") is True

    # Windows style backslashes
    assert is_protected_path("services\\core\\neural\\evolution\\models.py") is True
    assert is_protected_path("services\\core\\neural\\governance\\policy.py") is True

    # Permitted modules
    assert is_protected_path("services/core/neural/math/matrix_ops.py") is False
    assert is_protected_path("math_kernel.py") is False
    assert is_protected_path("test_math_kernel.py") is False


# -----------------------------------------------------------------------------
# 3. Proposal Engine (Empty list when no weakness detected, no fake fallback)
# -----------------------------------------------------------------------------
def test_proposal_engine_detect_weaknesses_returns_empty_list():
    """Validates that detect_weaknesses returns [] when no failures/bottlenecks are found."""
    engine = EvidenceBasedProposalEngine()

    # Empty log list
    weaknesses = engine.detect_weaknesses_sync(sample_failure_logs=[])
    assert weaknesses == []

    # Unrelated success log
    benign_logs = [
        {
            "task_id": "TASK-01",
            "title": "Healthy worker heartbeat",
            "verification_notes": "All green, exit 0",
            "retry_count": 0,
        }
    ]
    weaknesses = engine.detect_weaknesses_sync(sample_failure_logs=benign_logs)
    assert weaknesses == []

    # Verify no fake string_kernel.py fallback
    all_targets = [f for w in weaknesses for f in w.get("target_files", [])]
    assert "services/core/neural/utils/string_kernel.py" not in all_targets


# -----------------------------------------------------------------------------
# 4. Multi-Gate Verifier Benchmark Gate (Empirical execution & fail-closed)
# -----------------------------------------------------------------------------
def test_benchmark_gate_fails_closed_on_unexecuted_or_simulated_metrics(tmp_path: Path):
    """Validates that benchmark gate rejects unexecuted, simulated, or invalid metrics."""
    repo_dir = _init_test_git_repo(tmp_path / "repo_bench")
    staging_mgr = SelfEvolutionCandidateManager(repo_dir, tmp_path / "managed")
    verifier = SelfEvolutionMultiGateVerifier(staging_mgr)

    proposal = SelfEvolutionProposal(
        proposal_id="PROP-BENCH-TEST",
        title="Speedup Proposal",
        description="Benchmark gate test",
        category="performance",
        target_files=["math_ops.py"],
        root_cause_rationale="Vectorization",
        baseline_metrics={"execution_time_ms": 100.0, "error_rate": 0.05},
        target_goals={"execution_time_ms": 20.0, "min_speedup_ratio": 2.0, "error_rate": 0.01},
    )

    # 1. No metrics provided at all -> fails closed
    passed, comp, notes = verifier._run_benchmark_gate(proposal, candidate_metrics=None)
    assert passed is False
    assert "fail-closed" in notes.lower() or "no empirical benchmark" in notes.lower()

    # 2. Simulated flag -> fails closed
    passed, comp, notes = verifier._run_benchmark_gate(
        proposal, candidate_metrics={"execution_time_ms": 15.0, "simulated": True}
    )
    assert passed is False
    assert "simulated" in notes.lower()

    # 3. Explicitly unexecuted flag -> fails closed
    passed, comp, notes = verifier._run_benchmark_gate(
        proposal, candidate_metrics={"execution_time_ms": 15.0, "executed": False}
    )
    assert passed is False
    assert "unexecuted" in notes.lower()

    # 4. Invalid metric values (e.g. negative time or invalid error rate)
    passed, comp, notes = verifier._run_benchmark_gate(
        proposal, candidate_metrics={"execution_time_ms": -10.0}
    )
    assert passed is False
    assert "invalid" in notes.lower()

    # 5. Speedup below target goal -> fails
    passed, comp, notes = verifier._run_benchmark_gate(
        proposal, candidate_metrics={"execution_time_ms": 90.0, "error_rate": 0.0}
    )
    assert passed is False
    assert "below target goal" in notes.lower()


def test_benchmark_gate_runs_empirical_benchmark_command(tmp_path: Path):
    """Validates that benchmark gate can execute empirical benchmark command and measure duration."""
    repo_dir = _init_test_git_repo(tmp_path / "repo_bench_cmd")
    staging_mgr = SelfEvolutionCandidateManager(repo_dir, tmp_path / "managed_cmd")
    verifier = SelfEvolutionMultiGateVerifier(staging_mgr)

    proposal = SelfEvolutionProposal(
        proposal_id="PROP-BENCH-CMD",
        title="Empirical Command Proposal",
        description="Test empirical command execution",
        category="performance",
        target_files=["math_ops.py"],
        root_cause_rationale="Vectorization",
        baseline_metrics={"execution_time_ms": 1000.0, "error_rate": 0.05},
        target_goals={"execution_time_ms": 500.0, "min_speedup_ratio": 1.5, "error_rate": 0.01},
    )

    # Successful empirical command
    passed, comp, notes = verifier._run_benchmark_gate(
        proposal=proposal,
        candidate_path=repo_dir,
        benchmark_command=[sys.executable, "-c", "import time; time.sleep(0.01)"],
    )
    assert passed is True
    assert comp.get("empirical_check_passed") is True
    assert comp.get("empirical_measured_time_ms") > 0

    # Failing empirical command -> fails closed
    passed, comp, notes = verifier._run_benchmark_gate(
        proposal=proposal,
        candidate_path=repo_dir,
        benchmark_command=[sys.executable, "-c", "import sys; sys.exit(1)"],
    )
    assert passed is False
    assert "failed with code 1" in notes.lower()


# -----------------------------------------------------------------------------
# 5. CLI Self-Evolution Runner (Verification failure blocks promotion)
# -----------------------------------------------------------------------------
def test_cli_self_evolution_verification_failure_blocks_promotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Validates that even with auto_approve=True, a candidate failing verification is NEVER merged."""
    repo_dir = _init_test_git_repo(tmp_path / "repo_cli_fail")
    staging_dir = tmp_path / "worktrees_fail"
    staging_dir.mkdir()

    runner = CLIAutonomousEvolutionRunner(
        repository_root=repo_dir,
        staging_root=staging_dir,
        console=Console(record=True),
    )

    # Monkeypatch MultiGateVerifier to simulate a verification failure (e.g. regression failure)
    from neural.evolution.models import SelfEvolutionVerificationReceipt

    def mock_verify_candidate(*args, **kwargs):
        return SelfEvolutionVerificationReceipt(
            receipt_id="EVO-FAIL-RECEIPT",
            proposal_id="PROP-FAIL",
            candidate_branch="self_evolution/PROP-FAIL",
            passed=False,
            regression_passed=False,
            static_analysis_passed=True,
            ast_security_passed=True,
            benchmark_passed=False,
            benchmark_comparison={},
            artifact_digests={},
            diff_stat="",
            head_commit="abcdef",
            notes="Simulated regression failure",
            verifier_id="IndependentVerifier",
            signature="sig123",
        )

    monkeypatch.setattr(SelfEvolutionMultiGateVerifier, "verify_candidate", mock_verify_candidate)

    result = runner.run_evolution_cycle(auto_approve=True, interactive=False)

    assert result.verification_passed is False
    # CRITICAL INVARIANT: Never promote when verification failed
    assert result.promoted is False
    assert result.approved is False
