from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from neural.constants import DestructiveOperationType
from neural.db.repositories import ApprovalRepository, AuditRepository
from neural.engineering.models import (
    EngineeringStage,
)
from neural.engineering.pipeline import CollaborativeEngineeringPipeline
from neural.exceptions import HumanApprovalRequiredError
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
    repo = tmp_path / "source_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "ANCOS Pipeline Test")
    _git(repo, "config", "user.email", "pipeline@ancos.corp")
    (repo / "README.md").write_text("# Core Repository\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")
    return repo


@pytest.mark.asyncio
async def test_collaborative_engineering_pipeline_lifecycle(
    async_session: AsyncSession,
    tmp_path: Path,
):
    repo = _setup_repo(tmp_path)
    managed_worktrees = tmp_path / "worktrees"
    pipeline = CollaborativeEngineeringPipeline(repo, managed_worktrees)
    task_id = "TASK-COLLAB-01"

    # 1. Provision worktree
    worktree_receipt = pipeline.provision_worktree(task_id)
    assert pipeline.get_stage(task_id) == EngineeringStage.IMPLEMENTATION
    assert worktree_receipt.branch == f"ancos/task/{task_id}"

    # 2. Developer writes code with a bug
    initial_files = {
        "string_util.py": (
            "def reverse_words(s: str) -> str:\n"
            "    # Bug: reverses characters instead of words\n"
            "    return s[::-1]\n"
        ),
        "test_string_util.py": (
            "from string_util import reverse_words\n"
            "def test_reverse():\n"
            "    assert reverse_words('hello world') == 'world hello'\n"
        ),
    }
    dev_commit = pipeline.apply_developer_changes(
        task_id=task_id,
        files=initial_files,
        commit_message="Implement initial reverse_words",
        developer_id="ANC-EMP-DEV-01",
    )
    assert dev_commit
    assert pipeline.get_stage(task_id) == EngineeringStage.QA_REVIEW

    # 3. QA Engineer reviews and detects failure
    qa_receipt = pipeline.run_qa_review(task_id=task_id, qa_engineer_id="ANC-EMP-QA-01")
    assert not qa_receipt.passed
    assert qa_receipt.bug_report is not None
    assert pipeline.get_stage(task_id) == EngineeringStage.IMPLEMENTATION

    # 4. Bug remediation loop
    fix_files = {
        "string_util.py": (
            "def reverse_words(s: str) -> str:\n"
            "    words = s.split()\n"
            "    return ' '.join(reversed(words))\n"
        ),
    }
    fix_commit = pipeline.remediate_bug(
        task_id=task_id,
        bug_report=qa_receipt.bug_report,
        fix_files=fix_files,
        commit_message="Fix reverse_words to reverse words rather than characters",
        developer_id="ANC-EMP-DEV-01",
    )
    assert fix_commit != dev_commit

    # Re-verify by QA
    re_qa_receipt = pipeline.run_qa_review(task_id=task_id, qa_engineer_id="ANC-EMP-QA-01")
    assert re_qa_receipt.passed
    assert re_qa_receipt.bug_report is None
    assert pipeline.get_stage(task_id) == EngineeringStage.SECURITY_REVIEW

    # 5. Security review
    sec_receipt = pipeline.run_security_review(task_id=task_id, security_engineer_id="ANC-EMP-SEC-01")
    assert sec_receipt.passed
    assert sec_receipt.security_score >= 0.99
    assert pipeline.get_stage(task_id) == EngineeringStage.APPROVAL_GATE

    # 6. Pre-merge fail-closed verification
    approval_repo = ApprovalRepository(async_session)
    audit_repo = AuditRepository(async_session)

    with pytest.raises(HumanApprovalRequiredError):
        await pipeline.execute_merge(
            task_id=task_id,
            target_branch="main",
            qa_receipt=re_qa_receipt,
            security_receipt=sec_receipt,
            approval_id=None,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )

    # 7. Operator grants approval
    approval = await approval_repo.create(
        action_type=DestructiveOperationType.BRANCH_MERGE.value,
        description=f"Approve merge of task {task_id}",
        justification="Verified by QA and Security",
        requested_by="ANC-EMP-DEV-01",
    )
    await approval_repo.decide(
        approval_id=approval.id,
        decision="approved",
        decided_by="boss@ancos.corp",
    )

    # 8. Clean merge execution
    merge_receipt = await pipeline.execute_merge(
        task_id=task_id,
        target_branch="main",
        qa_receipt=re_qa_receipt,
        security_receipt=sec_receipt,
        approval_id=approval.id,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
        requested_by="boss@ancos.corp",
    )
    assert merge_receipt.task_id == task_id
    assert merge_receipt.verify_signature()
    assert pipeline.get_stage(task_id) == EngineeringStage.COMPLETED

    # Verify target main repository contains merged files
    assert (repo / "string_util.py").is_file()
    assert (repo / "test_string_util.py").is_file()
    assert not worktree_receipt.path.exists()
