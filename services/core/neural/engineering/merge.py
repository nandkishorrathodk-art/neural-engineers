from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Optional

from neural.constants import DestructiveOperationType
from neural.db.repositories import ApprovalRepository, AuditRepository
from neural.engineering.models import (
    MergeGateError,
    MergeReceipt,
    QAReviewReceipt,
    ReviewGateError,
    SecurityReviewReceipt,
)
from neural.engineering.qa_review import QAReviewEngine
from neural.engineering.worktrees import EngineeringWorktreeManager, WorktreeProvisionError
from neural.logger import get_logger
from neural.security.approval_gates import HumanApprovalGate

logger = get_logger("ancos.engineering.merge")

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class ApprovalGatedMergeManager:
    """
    Approval-Gated Integration & Clean Merge Manager.
    Enforces that code changes from isolated task worktrees can only be merged into
    target branches when:
    1. Git worktree provenance is cryptographically verified.
    2. Independent QA review has passed with a valid signature.
    3. Independent Security review has passed with a valid signature.
    4. HumanApprovalGate / Boss authority approves the destructive merge action.
    Merges without valid approval receipts strictly fail closed.
    """

    def __init__(self, worktree_manager: EngineeringWorktreeManager) -> None:
        self.worktree_manager = worktree_manager
        self.repository = worktree_manager.repository

    async def merge_task_branch(
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
        """
        Executes an approval-gated merge of a task branch into the target branch:
        - Fails closed if worktree provenance fails.
        - Fails closed if QA review is missing, failed, or signature invalid.
        - Fails closed if Security review is missing, failed, or signature invalid.
        - Fails closed with HumanApprovalRequiredError if approval is missing or unapproved.
        - When authorized, merges branch cleanly, records immutable MergeReceipt, and cleans up.
        """
        if not _BRANCH_RE.fullmatch(target_branch) or ".." in target_branch or target_branch.startswith("-") or target_branch.endswith(".lock"):
            raise MergeGateError(f"Invalid target branch name '{target_branch}'")

        if approval_repo is None:
            raise MergeGateError("Pre-merge validation failed: ApprovalRepository is required for approval-gated merge.")

        # 1. Verify worktree provenance
        try:
            worktree_receipt = self.worktree_manager.verify_receipt(task_id)
        except WorktreeProvisionError as exc:
            raise MergeGateError(f"Pre-merge validation failed: worktree provenance invalid: {exc}") from exc

        # 2. Verify QA review receipt
        if qa_receipt is None:
            raise ReviewGateError("Pre-merge validation failed: QA review receipt is required.")
        if qa_receipt.task_id != task_id:
            raise ReviewGateError("Pre-merge validation failed: QA review receipt task_id mismatch.")
        if not qa_receipt.passed:
            raise ReviewGateError("Pre-merge validation failed: QA review did not pass.")
        if not qa_receipt.verify_signature():
            raise ReviewGateError("Pre-merge validation failed: QA review receipt signature verification failed.")

        # 3. Verify live worktree files and HEAD commit match review receipts
        current_changed_files = self.worktree_manager.list_changed_files(task_id)
        live_digests = QAReviewEngine._compute_artifact_digests(
            worktree_receipt.path,
            current_changed_files,
        )
        if live_digests != qa_receipt.artifact_digests:
            raise ReviewGateError(
                "Pre-merge validation failed: live worktree files do not match QA review receipt artifact digests. "
                "Code has been modified after QA review."
            )
        current_head = self.worktree_manager.get_head_commit(task_id)
        if getattr(qa_receipt, "head_commit", None) and qa_receipt.head_commit != current_head:
            raise ReviewGateError(
                "Pre-merge validation failed: live worktree HEAD commit does not match QA review receipt. "
                "New commits were added after QA review."
            )

        # 4. Verify Security review receipt
        if security_receipt is None:
            raise ReviewGateError("Pre-merge validation failed: Security review receipt is required.")
        if security_receipt.task_id != task_id:
            raise ReviewGateError("Pre-merge validation failed: Security review receipt task_id mismatch.")
        if not security_receipt.passed:
            raise ReviewGateError("Pre-merge validation failed: Security review did not pass.")
        if not security_receipt.verify_signature():
            raise ReviewGateError("Pre-merge validation failed: Security review receipt signature verification failed.")
        if getattr(security_receipt, "head_commit", None) and security_receipt.head_commit != current_head:
            raise ReviewGateError(
                "Pre-merge validation failed: live worktree HEAD commit does not match Security review receipt. "
                "New commits were added after Security review."
            )

        # 5. Human Sovereignty Gate Enforcement
        action_type = DestructiveOperationType.BRANCH_MERGE.value
        details: dict[str, Any] = {
            "task_id": task_id,
            "source_branch": worktree_receipt.branch,
            "target_branch": target_branch,
            "base_commit": worktree_receipt.base_commit,
            "qa_receipt_id": qa_receipt.receipt_id,
            "security_receipt_id": security_receipt.receipt_id,
            "is_destructive": True,
            "destructive": True,
        }

        async def _perform_merge_operation() -> MergeReceipt:
            # Ensure source repository is clean before attempting merge (ignoring runtime test caches)
            repo_status_lines = [
                line.strip()
                for line in self._git("status", "--porcelain").splitlines()
                if line.strip()
            ]
            repo_dirty = []
            for line in repo_status_lines:
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    path_part = parts[1].strip()
                    if any(ign in path_part for ign in ["__pycache__", ".pytest_cache", ".coverage", ".ruff_cache"]):
                        continue
                repo_dirty.append(line)
            if repo_dirty:
                raise MergeGateError("Cannot execute merge: source repository working directory is dirty")

            # Ensure target branch is checked out in source repo
            current_branch = self._git("branch", "--show-current").strip()
            if current_branch != target_branch:
                self._git("checkout", target_branch)

            # Perform merge with --no-ff
            merge_msg = (
                f"Merge branch '{worktree_receipt.branch}' into '{target_branch}'\n\n"
                f"Task ID: {task_id}\n"
                f"QA Receipt: {qa_receipt.receipt_id}\n"
                f"Security Receipt: {security_receipt.receipt_id}\n"
                f"Approval ID: {approval_id or 'pre-authorized'}"
            )
            try:
                self._git(
                    "-c",
                    "user.name=ANCOS Integrator",
                    "-c",
                    f"user.email={requested_by}",
                    "merge",
                    "--no-ff",
                    worktree_receipt.branch,
                    "-m",
                    merge_msg,
                )
            except Exception as exc:
                try:
                    self._git("merge", "--abort")
                except Exception:
                    pass
                raise MergeGateError(f"Merge conflict or failure during git merge: {exc}") from exc

            merged_commit = self._git("rev-parse", "HEAD").strip().lower()

            # Create immutable MergeReceipt
            receipt = MergeReceipt.create(
                task_id=task_id,
                source_branch=worktree_receipt.branch,
                target_branch=target_branch,
                base_commit=worktree_receipt.base_commit,
                merged_commit=merged_commit,
                approved_by=requested_by,
                approval_id=approval_id or "gate-approved",
                qa_receipt_id=qa_receipt.receipt_id,
                security_receipt_id=security_receipt.receipt_id,
            )

            # Persist merge receipt in managed root
            merge_receipt_path = self.worktree_manager.managed_root / f"{task_id}.merge.json"
            try:
                with merge_receipt_path.open("x", encoding="utf-8") as stream:
                    json.dump(receipt.to_dict(), stream, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
            except OSError:
                pass

            # Clean up the worktree
            self.worktree_manager.remove_worktree(task_id, force=True)

            # Optionally delete branch
            if delete_task_branch:
                try:
                    self._git("branch", "-d", worktree_receipt.branch)
                except Exception:
                    pass

            return receipt

        # Gate execution: will fail closed if approval missing / not approved
        receipt = await HumanApprovalGate.execute_with_gate(
            action_type=action_type,
            requested_by=requested_by,
            description=f"Approval-gated merge of branch {worktree_receipt.branch} into {target_branch}",
            operation_callable=_perform_merge_operation,
            details=details,
            approval_id=approval_id,
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )

        return receipt

    def _git(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repository), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            detail = getattr(exc, "stderr", None)
            if isinstance(detail, bytes):
                detail = detail.decode("utf-8", errors="replace")
            raise MergeGateError(str(detail or exc).strip()) from exc
        return result.stdout
