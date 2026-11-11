from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.constants import DestructiveOperationType
from neural.db.repositories import ApprovalRepository, AuditRepository
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
    repo = tmp_path / "api_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "ANCOS API Tester")
    _git(repo, "config", "user.email", "tester@ancos.corp")
    (repo / "README.md").write_text("# ANCOS Master Repo Baseline\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial baseline commit")
    return repo


@pytest.fixture(autouse=True)
def reset_system_safety():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_self_evolution_api_endpoints_full_lifecycle(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """
    Tests FastAPI endpoints:
    1. Proposal creation with valid parameters & 403 on protected core path.
    2. Candidate branch staging in self_evolution/{proposal_id} & 403 on protected path edits.
    3. Multi-gate verification with passing benchmark comparison.
    4. Refusal of auto_promote without human approval token.
    5. Promotion into production branch with approved human approval token.
    6. Controlled rollback restoring pinned pre-evolution commit.
    """
    repo = _setup_git_repository(tmp_path)
    monkeypatch.chdir(repo)

    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # =====================================================================
        # 1. Proposal Creation & Protected Path Rejection
        # =====================================================================
        # Attempt proposal targeting protected core path -> must return 403
        bad_prop_payload = {
            "title": "Bypass Kill Switch",
            "description": "Unauthorized tamper attempt",
            "category": "optimization",
            "target_files": ["services/core/neural/security/kill_switch.py"],
            "root_cause_rationale": "Testing safety gate",
            "baseline_metrics": {"latency_ms": 100.0},
            "target_goals": {"latency_ms": 10.0},
            "proposer_id": "ANC-EMP-ATTACKER",
        }
        res_bad_prop = await client.post("/evolution/proposals", json=bad_prop_payload)
        assert res_bad_prop.status_code == 403
        assert "strictly prohibited" in res_bad_prop.json()["detail"].lower()

        # Valid proposal creation
        good_prop_payload = {
            "title": "Fast Token Matrix Kernel",
            "description": "Vectorized token math kernel",
            "category": "performance",
            "target_files": ["token_kernel.py"],
            "root_cause_rationale": "High latency in string token matrix multiplication",
            "baseline_metrics": {"execution_time_ms": 90.0, "error_rate": 0.05, "benchmark_throughput_ops_sec": 600.0},
            "target_goals": {"execution_time_ms": 25.0, "min_speedup_ratio": 2.5, "error_rate": 0.0},
            "proposer_id": "ANC-EMP-LEAD-01",
        }
        res_prop = await client.post("/evolution/proposals", json=good_prop_payload)
        assert res_prop.status_code == 200
        prop_data = res_prop.json()
        proposal_id = prop_data["proposal_id"]
        assert proposal_id.startswith("PROP-EVO-")
        assert prop_data["status"] == "draft"
        assert prop_data["signature"] is not None

        # Query proposal by ID
        res_get_prop = await client.get(f"/evolution/proposals/{proposal_id}")
        assert res_get_prop.status_code == 200
        assert res_get_prop.json()["proposal_id"] == proposal_id

        # =====================================================================
        # 2. Candidate Branch Staging & Protected Path Modification Rejection
        # =====================================================================
        # Modification attempt touching protected core file in staging -> 403
        bad_stage_payload = {
            "files": {"services/core/neural/security/approval_gates.py": "# bypass"},
            "commit_message": "Tamper approval gate",
            "author_id": "ANC-EMP-ATTACKER",
        }
        res_bad_stage = await client.post(f"/evolution/proposals/{proposal_id}/stage", json=bad_stage_payload)
        assert res_bad_stage.status_code == 403
        assert "strictly prohibited" in res_bad_stage.json()["detail"].lower()

        # Valid candidate code
        kernel_code = (
            "def multiply_tokens(a: list[float], b: list[float]) -> list[float]:\n"
            "    return [x * y for x, y in zip(a, b)]\n"
        )
        test_code = (
            "from token_kernel import multiply_tokens\n\n"
            "def test_multiply_tokens():\n"
            "    assert multiply_tokens([2.0, 3.0], [4.0, 5.0]) == [8.0, 15.0]\n"
        )
        good_stage_payload = {
            "files": {"token_kernel.py": kernel_code, "test_token_kernel.py": test_code},
            "commit_message": "Implement vectorized multiply_tokens kernel with tests",
            "author_id": "ANC-EMP-LEAD-01",
        }
        res_stage = await client.post(f"/evolution/proposals/{proposal_id}/stage", json=good_stage_payload)
        assert res_stage.status_code == 200
        stage_data = res_stage.json()
        assert stage_data["proposal_id"] == proposal_id
        assert stage_data["branch"] == f"self_evolution/{proposal_id}"
        assert stage_data["head_commit"] is not None

        # =====================================================================
        # 3. Multi-Gate Independent Verification
        # =====================================================================
        verify_payload = {
            "candidate_benchmark_metrics": {
                "execution_time_ms": 22.0,
                "error_rate": 0.0,
                "benchmark_throughput_ops_sec": 2400.0,
            }
        }
        res_verify = await client.post(f"/evolution/proposals/{proposal_id}/verify", json=verify_payload)
        assert res_verify.status_code == 200
        verif_data = res_verify.json()
        assert verif_data["passed"] is True
        assert verif_data["regression_passed"] is True
        assert verif_data["static_analysis_passed"] is True
        assert verif_data["ast_security_passed"] is True
        assert verif_data["benchmark_passed"] is True
        assert verif_data["verifier_id"] == "IndependentDeliverableVerifier"
        assert verif_data["receipt_id"].startswith("EVO-VERIF-")
        verif_receipt_id = verif_data["receipt_id"]

        # =====================================================================
        # 4. Refusal of Auto-Promotion Without Human Approval
        # =====================================================================
        res_auto_promo = await client.post(
            f"/evolution/proposals/{proposal_id}/promote",
            json={"target_branch": "main", "auto_promote": True},
        )
        assert res_auto_promo.status_code == 200
        auto_data = res_auto_promo.json()
        assert auto_data["activation_status"] == "approval_required"
        assert auto_data["eligible_for_promotion"] is True
        assert auto_data["auto_promote_blocked"] is True
        assert not (repo / "token_kernel.py").exists()

        # =====================================================================
        # 5. Staged Promotion With Approved Human Token
        # =====================================================================
        pre_evolution_commit = _git(repo, "rev-parse", "HEAD")

        approval_repo = ApprovalRepository(async_session)
        approval = await approval_repo.create(
            action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
            description=f"Approve self-evolution candidate {proposal_id}",
            justification="Passed all 4 verification gates with 4.1x speedup",
            requested_by="ANC-EMP-LEAD-01",
            details={"proposal_id": proposal_id, "verification_receipt_id": verif_receipt_id},
        )
        await approval_repo.decide(approval.id, decision="approved", decided_by="owner@ancos.corp")

        res_promo = await client.post(
            f"/evolution/proposals/{proposal_id}/promote",
            json={
                "target_branch": "main",
                "approval_id": approval.id,
                "requested_by": "ANC-EMP-LEAD-01",
            },
        )
        assert res_promo.status_code == 200
        promo_data = res_promo.json()
        assert promo_data["activation_status"] == "promoted"
        assert promo_data["pre_evolution_commit"] == pre_evolution_commit.lower()
        assert promo_data["promoted_commit"] != pre_evolution_commit.lower()
        assert (repo / "token_kernel.py").is_file()

        # =====================================================================
        # 6. Controlled Rollback
        # =====================================================================
        # Inject regression in main
        (repo / "token_kernel.py").write_text("def multiply_tokens(a, b):\n    raise RuntimeError('FAULT')\n", encoding="utf-8")
        _git(repo, "add", "token_kernel.py")
        _git(repo, "commit", "-m", "Injected regression")
        bad_head = _git(repo, "rev-parse", "HEAD")

        rollback_payload = {
            "target_branch": "main",
            "pre_evolution_commit": pre_evolution_commit,
            "anomaly_detected": {
                "type": "POST_PROMOTION_REGRESSION_DETECTED",
                "severity": "CRITICAL",
                "details": "Runtime crash in multiply_tokens",
            },
            "requested_by": "PostPromotionHealthMonitor",
        }
        res_rb = await client.post(f"/evolution/proposals/{proposal_id}/rollback", json=rollback_payload)
        assert res_rb.status_code == 200
        rb_data = res_rb.json()
        assert rb_data["rolled_back_commit"] == bad_head.lower()
        assert rb_data["restored_commit"] == pre_evolution_commit.lower()
        assert not (repo / "token_kernel.py").exists()
        assert _git(repo, "rev-parse", "HEAD") == pre_evolution_commit

        audit_repo = AuditRepository(async_session)
        logs = await audit_repo.list_all(limit=10)
        assert any(log_entry.action == "ROLLBACK_RESTORED_PRISTINE_STATE" for log_entry in logs)
