from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from neural.constants import DestructiveOperationType
from neural.db.repositories import ApprovalRepository, AuditRepository
from neural.engineering.merge import ApprovalGatedMergeManager
from neural.engineering.models import (
    BugReport,
    MergeGateError,
    MergeReceipt,
    QAReviewReceipt,
    ReviewGateError,
    SecurityReviewReceipt,
)
from neural.engineering.pipeline import CollaborativeEngineeringPipeline
from neural.engineering.qa_review import QAReviewEngine
from neural.engineering.security_review import SecurityReviewEngine
from neural.engineering.worktrees import EngineeringWorktreeManager, WorktreeProvisionError
from neural.exceptions import HumanApprovalRequiredError
from neural.security.approval_gates import HumanApprovalGate
from sqlalchemy.ext.asyncio import AsyncSession


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "-c", "user.name=ANCOS Test", "-c", "user.email=test@ancos.corp", "config", "user.name", "ANCOS Test")
    _git(repo, "config", "user.email", "test@ancos.corp")
    (repo / "README.md").write_text("# Project\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")
    return repo


def test_worktree_manager_diff_commit_and_cleanup(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)

    receipt = manager.create("TASK-DIFF-01")
    assert receipt.branch == "ancos/task/TASK-DIFF-01"

    # Write a new file in the worktree
    (receipt.path / "module.py").write_text("def hello(): return 'world'\n", encoding="utf-8")
    commit_sha = manager.commit_changes("TASK-DIFF-01", "Add hello module", "TestDev", "dev@ancos.corp")
    assert commit_sha

    changed = manager.list_changed_files("TASK-DIFF-01")
    assert "module.py" in changed

    diff = manager.get_diff("TASK-DIFF-01")
    assert "def hello():" in diff

    head = manager.get_head_commit("TASK-DIFF-01")
    assert head == commit_sha

    manager.remove_worktree("TASK-DIFF-01")
    assert not receipt.path.exists()


def test_qa_review_engine_detects_failure_and_produces_bug_report(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-QA-FAIL")

    # Write code and a failing test
    (receipt.path / "calculator.py").write_text(
        "def divide(a, b):\n    return a / b\n",
        encoding="utf-8",
    )
    (receipt.path / "test_calculator.py").write_text(
        "from calculator import divide\n"
        "def test_div():\n"
        "    assert divide(10, 2) == 5\n"
        "def test_div_zero():\n"
        "    assert divide(5, 0) == 0  # Intentionally failing assertion\n",
        encoding="utf-8",
    )
    manager.commit_changes("TASK-QA-FAIL", "Add divide with failing test")

    qa_engine = QAReviewEngine()
    qa_receipt = qa_engine.review_task_worktree(
        worktree_manager=manager,
        task_id="TASK-QA-FAIL",
        qa_engineer_id="ANC-EMP-QA-01",
    )

    assert not qa_receipt.passed
    assert qa_receipt.test_exit_code != 0
    assert qa_receipt.bug_report is not None
    assert "TASK-QA-FAIL" == qa_receipt.bug_report.task_id
    assert qa_receipt.bug_report.reporter_id == "ANC-EMP-QA-01"
    assert qa_receipt.verify_signature()


def test_qa_review_engine_passes_clean_code(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-QA-PASS")

    (receipt.path / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (receipt.path / "test_calculator.py").write_text(
        "from calculator import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    manager.commit_changes("TASK-QA-PASS", "Add clean add function")

    qa_engine = QAReviewEngine()
    qa_receipt = qa_engine.review_task_worktree(
        worktree_manager=manager,
        task_id="TASK-QA-PASS",
        qa_engineer_id="ANC-EMP-QA-01",
    )

    assert qa_receipt.passed
    assert qa_receipt.test_exit_code == 0
    assert qa_receipt.bug_report is None
    assert qa_receipt.verify_signature()
    assert "calculator.py" in qa_receipt.artifact_digests


def test_security_review_engine_detects_vulnerabilities(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-SEC-VULN")

    (receipt.path / "unsafe.py").write_text(
        "import os\n"
        "api_key = 'super_secret_unmasked_api_key_12345'\n"
        "def run_command(cmd):\n"
        "    os.system(cmd)\n"
        "    eval(cmd)\n",
        encoding="utf-8",
    )
    manager.commit_changes("TASK-SEC-VULN", "Add unsafe code")

    sec_engine = SecurityReviewEngine()
    sec_receipt = sec_engine.review_task_worktree(
        worktree_manager=manager,
        task_id="TASK-SEC-VULN",
        security_engineer_id="ANC-EMP-SEC-01",
    )

    assert not sec_receipt.passed
    assert sec_receipt.security_score < 0.8
    assert len(sec_receipt.vulnerabilities_found) >= 3  # secret, os.system, eval
    assert sec_receipt.verify_signature()


def test_security_review_engine_passes_safe_code(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-SEC-SAFE")

    (receipt.path / "safe.py").write_text(
        "import math\n"
        "def compute_sqrt(x: float) -> float:\n"
        "    if x < 0:\n"
        "        raise ValueError('Cannot compute sqrt of negative number')\n"
        "    return math.sqrt(x)\n",
        encoding="utf-8",
    )
    manager.commit_changes("TASK-SEC-SAFE", "Add safe sqrt")

    sec_engine = SecurityReviewEngine()
    sec_receipt = sec_engine.review_task_worktree(
        worktree_manager=manager,
        task_id="TASK-SEC-SAFE",
        security_engineer_id="ANC-EMP-SEC-01",
    )

    assert sec_receipt.passed
    assert sec_receipt.security_score >= 0.99
    assert len(sec_receipt.vulnerabilities_found) == 0
    assert sec_receipt.verify_signature()


@pytest.mark.asyncio
async def test_merge_fails_closed_without_approval(async_session: AsyncSession, tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-MERGE-GATE")

    (receipt.path / "feature.py").write_text("FEATURE = True\n", encoding="utf-8")
    (receipt.path / "test_feature.py").write_text("def test_f(): assert True\n", encoding="utf-8")
    manager.commit_changes("TASK-MERGE-GATE", "Add feature")

    qa_engine = QAReviewEngine()
    qa_receipt = qa_engine.review_task_worktree(manager, "TASK-MERGE-GATE", "ANC-EMP-QA-01")
    assert qa_receipt.passed

    sec_engine = SecurityReviewEngine()
    sec_receipt = sec_engine.review_task_worktree(manager, "TASK-MERGE-GATE", "ANC-EMP-SEC-01")
    assert sec_receipt.passed

    merge_manager = ApprovalGatedMergeManager(manager)
    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    # Attempting merge without pre-approved approval_id MUST fail closed!
    with pytest.raises(HumanApprovalRequiredError):
        await merge_manager.merge_task_branch(
            task_id="TASK-MERGE-GATE",
            target_branch="main",
            qa_receipt=qa_receipt,
            security_receipt=sec_receipt,
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )


@pytest.mark.asyncio
async def test_merge_succeeds_with_valid_human_approval(async_session: AsyncSession, tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-MERGE-APPROVED")

    (receipt.path / "feature.py").write_text("VALUE = 42\n", encoding="utf-8")
    (receipt.path / "test_feature.py").write_text("def test_v(): assert True\n", encoding="utf-8")
    manager.commit_changes("TASK-MERGE-APPROVED", "Implement feature value 42")

    qa_receipt = QAReviewEngine().review_task_worktree(manager, "TASK-MERGE-APPROVED", "ANC-EMP-QA-01")
    sec_receipt = SecurityReviewEngine().review_task_worktree(manager, "TASK-MERGE-APPROVED", "ANC-EMP-SEC-01")

    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    # Operator creates and approves the request
    approval = await approval_repo.create(
        action_type=DestructiveOperationType.BRANCH_MERGE.value,
        description="Merge feature into main",
        justification="Verified by QA and Security",
        requested_by="ANC-EMP-DEV-01",
    )
    await approval_repo.decide(
        approval_id=approval.id,
        decision="approved",
        decided_by="owner@ancos.corp",
    )

    merge_manager = ApprovalGatedMergeManager(manager)
    merge_receipt = await merge_manager.merge_task_branch(
        task_id="TASK-MERGE-APPROVED",
        target_branch="main",
        qa_receipt=qa_receipt,
        security_receipt=sec_receipt,
        approval_id=approval.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
        requested_by="owner@ancos.corp",
    )

    assert merge_receipt.task_id == "TASK-MERGE-APPROVED"
    assert merge_receipt.target_branch == "main"
    assert merge_receipt.verify_signature()
    assert (repo / "feature.py").is_file()
    assert (repo / "feature.py").read_text(encoding="utf-8") == "VALUE = 42\n"


@pytest.mark.asyncio
async def test_approval_gate_fails_closed_without_repo_even_with_approval_id():
    executed = False

    async def _action():
        nonlocal executed
        executed = True
        return "success"

    with pytest.raises(HumanApprovalRequiredError, match="cannot verify approval"):
        await HumanApprovalGate.execute_with_gate(
            action_type="branch_merge",
            requested_by="attacker",
            description="attempted unverified bypass",
            operation_callable=_action,
            approval_id="fake-approval-id",
            approval_repo=None,
        )
    assert not executed


def test_pipeline_rejects_developer_path_traversal(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    pipeline = CollaborativeEngineeringPipeline(repo, managed_root)
    pipeline.provision_worktree("TASK-TRAVERSAL-01")

    with pytest.raises(WorktreeProvisionError, match="escapes task worktree root"):
        pipeline.apply_developer_changes(
            task_id="TASK-TRAVERSAL-01",
            files={"../../escaped_file.py": "malicious = True\n"},
            commit_message="Attempted traversal",
            developer_id="ANC-EMP-DEV-01",
        )


@pytest.mark.asyncio
async def test_merge_fails_if_worktree_modified_after_qa_review(async_session: AsyncSession, tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-TAMPER-01")

    (receipt.path / "module.py").write_text("VALID = True\n", encoding="utf-8")
    (receipt.path / "test_module.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    manager.commit_changes("TASK-TAMPER-01", "Initial commit")

    qa_receipt = QAReviewEngine().review_task_worktree(manager, "TASK-TAMPER-01", "ANC-EMP-QA-01")
    sec_receipt = SecurityReviewEngine().review_task_worktree(manager, "TASK-TAMPER-01", "ANC-EMP-SEC-01")

    # Developer tampers with file after QA approval!
    (receipt.path / "module.py").write_text("VALID = False  # Tampered\n", encoding="utf-8")
    manager.commit_changes("TASK-TAMPER-01", "Tampered commit")

    approval_repo = ApprovalRepository(async_session)
    approval = await approval_repo.create(
        action_type=DestructiveOperationType.BRANCH_MERGE.value,
        description="Merge tampered branch",
        justification="Testing tamper detection",
        requested_by="ANC-EMP-DEV-01",
    )
    await approval_repo.decide(approval.id, "approved", "boss@ancos.corp")

    merge_manager = ApprovalGatedMergeManager(manager)
    with pytest.raises(ReviewGateError, match="Code has been modified after QA review|HEAD commit does not match"):
        await merge_manager.merge_task_branch(
            task_id="TASK-TAMPER-01",
            target_branch="main",
            qa_receipt=qa_receipt,
            security_receipt=sec_receipt,
            approval_id=approval.id,
            approval_repo=approval_repo,
        )


@pytest.mark.asyncio
async def test_merge_handles_conflict_and_aborts_cleanly(async_session: AsyncSession, tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-CONFLICT-01")

    # Change README.md in task worktree
    (receipt.path / "README.md").write_text("# Conflicting task title\n", encoding="utf-8")
    (receipt.path / "test_dummy.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    manager.commit_changes("TASK-CONFLICT-01", "Task change in README")

    qa_receipt = QAReviewEngine().review_task_worktree(manager, "TASK-CONFLICT-01", "ANC-EMP-QA-01")
    sec_receipt = SecurityReviewEngine().review_task_worktree(manager, "TASK-CONFLICT-01", "ANC-EMP-SEC-01")

    # Main repo advances on README.md creating a direct merge conflict!
    (repo / "README.md").write_text("# Conflicting upstream title\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Upstream README change")

    approval_repo = ApprovalRepository(async_session)
    approval = await approval_repo.create(
        action_type=DestructiveOperationType.BRANCH_MERGE.value,
        description="Merge conflicting branch",
        justification="Testing conflict handling",
        requested_by="ANC-EMP-DEV-01",
    )
    await approval_repo.decide(approval.id, "approved", "boss@ancos.corp")

    merge_manager = ApprovalGatedMergeManager(manager)
    with pytest.raises(MergeGateError, match="Merge conflict or failure during git merge"):
        await merge_manager.merge_task_branch(
            task_id="TASK-CONFLICT-01",
            target_branch="main",
            qa_receipt=qa_receipt,
            security_receipt=sec_receipt,
            approval_id=approval.id,
            approval_repo=approval_repo,
        )

    # Confirm repository was aborted cleanly out of MERGE_HEAD!
    status = _git(repo, "status", "--porcelain")
    assert status == "", "Repository must be clean after aborted conflict merge"


def test_security_ast_detects_direct_subprocess_and_system_calls(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-SEC-DIRECT")

    # Test direct imports: 'from subprocess import run', 'from os import system'
    (receipt.path / "direct_calls.py").write_text(
        "from subprocess import run\n"
        "from os import system\n"
        "client_secret = 'super_secret_client_token_123456'\n"
        "def execute(cmd):\n"
        "    system(cmd)\n"
        "    run(cmd, shell=True)\n",
        encoding="utf-8",
    )
    manager.commit_changes("TASK-SEC-DIRECT", "Add direct calls")

    sec_engine = SecurityReviewEngine()
    receipt_sec = sec_engine.review_task_worktree(manager, "TASK-SEC-DIRECT", "ANC-EMP-SEC-01")

    assert not receipt_sec.passed
    vuln_types = [v["type"] for v in receipt_sec.vulnerabilities_found]
    assert "dangerous_function" in vuln_types  # system
    assert "shell_true_injection" in vuln_types  # run(shell=True)
    assert "hardcoded_secret" in vuln_types  # client_secret


def test_qa_and_security_reject_uncommitted_worktree_changes(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)
    receipt = manager.create("TASK-DIRTY-01")

    # Leave file uncommitted on disk
    (receipt.path / "uncommitted.py").write_text("x = 1\n", encoding="utf-8")

    qa_engine = QAReviewEngine()
    with pytest.raises(ReviewGateError, match="task worktree has uncommitted changes"):
        qa_engine.review_task_worktree(manager, "TASK-DIRTY-01", "ANC-EMP-QA-01")

    sec_engine = SecurityReviewEngine()
    with pytest.raises(ReviewGateError, match="task worktree has uncommitted changes"):
        sec_engine.review_task_worktree(manager, "TASK-DIRTY-01", "ANC-EMP-SEC-01")


def test_worktree_manager_rejects_reserved_windows_names(tmp_path: Path):
    repo = _setup_repo(tmp_path)
    managed_root = tmp_path / "worktrees"
    manager = EngineeringWorktreeManager(repo, managed_root)

    for reserved in ["CON", "nul", "prn", "AUX", "COM1"]:
        with pytest.raises(WorktreeProvisionError, match="reserved device name"):
            manager.create(reserved)


def test_receipt_models_serialization_and_from_dict():
    bug = BugReport(
        bug_id="BUG-1234",
        task_id="TASK-MODELS-01",
        reporter_id="ANC-EMP-QA-01",
        reproduction_command="pytest",
        failure_summary="Test failure",
        failed_tests=["test_fail"],
        error_details="assert 1 == 2",
        affected_files=["app.py"],
        suggested_action="Fix assertion",
    )
    bug_dict = bug.to_dict()
    reloaded_bug = BugReport.from_dict(bug_dict)
    assert reloaded_bug.bug_id == bug.bug_id
    assert reloaded_bug.failed_tests == ["test_fail"]

    qa = QAReviewReceipt.create(
        task_id="TASK-MODELS-01",
        qa_engineer_id="ANC-EMP-QA-01",
        passed=True,
        test_exit_code=0,
        tests_run=5,
        tests_passed=5,
        tests_failed=0,
        diff_stat="1 file changed",
        artifact_digests={"app.py": "abc123hash"},
        head_commit="f" * 40,
    )
    qa_dict = qa.to_dict()
    reloaded_qa = QAReviewReceipt.from_dict(qa_dict)
    assert reloaded_qa.verify_signature()
    assert reloaded_qa.head_commit == "f" * 40

    sec = SecurityReviewReceipt.create(
        task_id="TASK-MODELS-01",
        security_engineer_id="ANC-EMP-SEC-01",
        passed=True,
        vulnerabilities_found=[],
        scanned_files_count=1,
        security_score=1.0,
        head_commit="f" * 40,
    )
    sec_dict = sec.to_dict()
    reloaded_sec = SecurityReviewReceipt.from_dict(sec_dict)
    assert reloaded_sec.verify_signature()
    assert reloaded_sec.head_commit == "f" * 40

    merge = MergeReceipt.create(
        task_id="TASK-MODELS-01",
        source_branch="ancos/task/TASK-MODELS-01",
        target_branch="main",
        base_commit="a" * 40,
        merged_commit="b" * 40,
        approved_by="boss@ancos.corp",
        approval_id="APP-123",
        qa_receipt_id=qa.receipt_id,
        security_receipt_id=sec.receipt_id,
    )
    merge_dict = merge.to_dict()
    reloaded_merge = MergeReceipt.from_dict(merge_dict)
    assert reloaded_merge.verify_signature()
    assert reloaded_merge.receipt_id == merge.receipt_id

