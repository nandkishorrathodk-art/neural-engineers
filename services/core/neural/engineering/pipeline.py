from __future__ import annotations

from pathlib import Path
from typing import Optional

from neural.db.repositories import ApprovalRepository, AuditRepository
from neural.engineering.merge import ApprovalGatedMergeManager
from neural.engineering.models import (
    BugReport,
    EngineeringStage,
    MergeReceipt,
    QAReviewReceipt,
    SecurityReviewReceipt,
)
from neural.engineering.qa_review import QAReviewEngine
from neural.engineering.security_review import SecurityReviewEngine
from neural.engineering.worktrees import (
    EngineeringWorktreeManager,
    WorktreeProvisionError,
    WorktreeReceipt,
)
from neural.logger import get_logger

logger = get_logger("ancos.engineering.pipeline")


class CollaborativeEngineeringPipeline:
    """
    Multi-Employee Collaborative Engineering Pipeline.
    Coordinates the complete software development lifecycle:
    1. Isolated Git Worktree Provisioning (EngineeringWorktreeManager)
    2. Developer Implementation & Commits
    3. QA Engineer Diff Review & Test Execution (QAReviewEngine)
    4. Bug Remediation Loop upon Detected Regressions
    5. Security Engineer AST & Vulnerability Review (SecurityReviewEngine)
    6. Approval-Gated Merge into Target Branch (ApprovalGatedMergeManager)
    """

    def __init__(
        self,
        repository_path: str | Path,
        managed_worktree_root: str | Path,
    ) -> None:
        self.worktree_manager = EngineeringWorktreeManager(repository_path, managed_worktree_root)
        self.qa_engine = QAReviewEngine()
        self.security_engine = SecurityReviewEngine()
        self.merge_manager = ApprovalGatedMergeManager(self.worktree_manager)
        self._current_stage: dict[str, EngineeringStage] = {}

    def get_stage(self, task_id: str) -> EngineeringStage:
        return self._current_stage.get(task_id, EngineeringStage.SPECIFICATION)

    def provision_worktree(self, task_id: str, base_ref: str = "HEAD") -> WorktreeReceipt:
        """Step 1: Provision isolated task worktree with pinned base commit."""
        receipt = self.worktree_manager.create(task_id, base_ref=base_ref)
        self._current_stage[task_id] = EngineeringStage.IMPLEMENTATION
        logger.info("Provisioned isolated engineering worktree", task_id=task_id, branch=receipt.branch)
        return receipt

    def apply_developer_changes(
        self,
        task_id: str,
        files: dict[str, str],
        commit_message: str,
        developer_id: str,
        developer_email: str = "dev@ancos.corp",
    ) -> str:
        """Step 2: Developer writes code to the worktree and creates a Git commit."""
        receipt = self.worktree_manager.verify_receipt(task_id)
        worktree_path = receipt.path

        for rel_path, content in files.items():
            target_file = (worktree_path / rel_path).resolve()
            if not self.worktree_manager._is_within(target_file, worktree_path):
                raise WorktreeProvisionError(f"Target file path '{rel_path}' escapes task worktree root")
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(content, encoding="utf-8")

        commit_hash = self.worktree_manager.commit_changes(
            task_id=task_id,
            message=commit_message,
            author_name=developer_id,
            author_email=developer_email,
        )
        self._current_stage[task_id] = EngineeringStage.QA_REVIEW
        logger.info("Developer committed changes to worktree", task_id=task_id, commit=commit_hash)
        return commit_hash

    def run_qa_review(
        self,
        task_id: str,
        qa_engineer_id: str,
        test_command: Optional[list[str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> QAReviewReceipt:
        """Step 3: QA Engineer inspects diff and executes automated verification in clean sandbox."""
        receipt = self.qa_engine.review_task_worktree(
            worktree_manager=self.worktree_manager,
            task_id=task_id,
            qa_engineer_id=qa_engineer_id,
            test_command=test_command,
            timeout_seconds=timeout_seconds,
        )
        if not receipt.passed:
            self._current_stage[task_id] = EngineeringStage.IMPLEMENTATION  # Return to dev for fix
        else:
            self._current_stage[task_id] = EngineeringStage.SECURITY_REVIEW
        return receipt

    def remediate_bug(
        self,
        task_id: str,
        bug_report: BugReport,
        fix_files: dict[str, str],
        commit_message: str,
        developer_id: str,
        developer_email: str = "dev@ancos.corp",
    ) -> str:
        """Step 4: Developer receives structured bug report and commits remediation fix."""
        logger.info(
            "Developer applying bug remediation based on QA feedback",
            task_id=task_id,
            bug_id=bug_report.bug_id,
            developer=developer_id,
        )
        return self.apply_developer_changes(
            task_id=task_id,
            files=fix_files,
            commit_message=commit_message,
            developer_id=developer_id,
            developer_email=developer_email,
        )

    def run_security_review(
        self,
        task_id: str,
        security_engineer_id: str,
    ) -> SecurityReviewReceipt:
        """Step 5: Security Reviewer inspects diff and files for vulnerabilities and secrets."""
        receipt = self.security_engine.review_task_worktree(
            worktree_manager=self.worktree_manager,
            task_id=task_id,
            security_engineer_id=security_engineer_id,
        )
        if receipt.passed:
            self._current_stage[task_id] = EngineeringStage.APPROVAL_GATE
        else:
            self._current_stage[task_id] = EngineeringStage.FAILED
        return receipt

    async def execute_merge(
        self,
        task_id: str,
        target_branch: str = "main",
        qa_receipt: Optional[QAReviewReceipt] = None,
        security_receipt: Optional[SecurityReviewReceipt] = None,
        approval_id: Optional[str] = None,
        approval_repo: Optional[ApprovalRepository] = None,
        audit_repo: Optional[AuditRepository] = None,
        requested_by: str = "engineering_pipeline",
        delete_task_branch: bool = False,
    ) -> MergeReceipt:
        """Step 6: Approval-Gated Merge into Target Branch."""
        receipt = await self.merge_manager.merge_task_branch(
            task_id=task_id,
            target_branch=target_branch,
            qa_receipt=qa_receipt,
            security_receipt=security_receipt,
            approval_id=approval_id,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
            requested_by=requested_by,
            delete_task_branch=delete_task_branch,
        )
        self._current_stage[task_id] = EngineeringStage.COMPLETED
        logger.info("Successfully merged task branch into target", task_id=task_id, target=target_branch)
        return receipt
