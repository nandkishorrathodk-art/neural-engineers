from __future__ import annotations

import re
import subprocess
from typing import Any, Optional

from neural.constants import ApprovalStatus, DestructiveOperationType
from neural.db.repositories import ApprovalRepository, AuditRepository
from neural.evolution.candidate_staging import SelfEvolutionCandidateManager
from neural.evolution.models import (
    PromotionGateError,
    ProtectedPathViolationError,
    SelfEvolutionPromotionReceipt,
    SelfEvolutionProposal,
    SelfEvolutionVerificationReceipt,
    is_protected_path,
)
from neural.exceptions import HumanApprovalRequiredError
from neural.logger import get_logger

logger = get_logger("ancos.evolution.promotion_manager")

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class SelfEvolutionPromotionManager:
    """
    Approval-Gated Staged Promotion Manager for Self-Evolution.
    Enforces strict Human Sovereignty:
    1. Automatic promotion into production code is strictly prohibited, even if auto_promote=True
       is requested. Eligible assessments report 'approval_required'.
    2. Promotion into production requires explicit consumption of a cryptographically signed
       human approval token (HumanApprovalGate).
    3. Pins the pre-evolution commit state prior to promotion for deterministic disaster recovery.
    4. Merges verified candidate branch (self_evolution/{proposal_id}) into target production branch.
    5. Issues tamper-evident SelfEvolutionPromotionReceipt and records immutable audit log.
    """

    def __init__(self, candidate_manager: SelfEvolutionCandidateManager) -> None:
        self.candidate_manager = candidate_manager
        self.repository = candidate_manager.repository

    def assess_promotion_readiness(
        self,
        proposal: SelfEvolutionProposal,
        verification_receipt: SelfEvolutionVerificationReceipt,
        auto_promote: bool = False,
    ) -> dict[str, Any]:
        """
        Assesses whether a candidate proposal meets technical eligibility for promotion.
        CRITICAL INVARIANT: Automatic promotion into production code is strictly prohibited.
        Even when auto_promote=True is requested, activation_status is ALWAYS 'approval_required'.
        """
        if not verification_receipt.passed:
            return {
                "proposal_id": proposal.proposal_id,
                "eligible_for_promotion": False,
                "activation_status": "rejected",
                "auto_promote_blocked": False,
                "rejection_reasons": ["Multi-gate verification did not pass."],
            }

        if not verification_receipt.verify_signature():
            return {
                "proposal_id": proposal.proposal_id,
                "eligible_for_promotion": False,
                "activation_status": "rejected",
                "auto_promote_blocked": False,
                "rejection_reasons": ["Verification receipt signature verification failed."],
            }

        # Candidate is technically eligible, but human approval is mandatory
        return {
            "proposal_id": proposal.proposal_id,
            "eligible_for_promotion": True,
            "activation_status": "approval_required",
            "auto_promote_blocked": True if auto_promote else False,
            "message": (
                "Technical verification passed. Automatic promotion into production is strictly prohibited. "
                "Production code modification requires an explicit human approval token from HumanApprovalGate."
            ),
        }

    async def execute_promotion(
        self,
        proposal_id: str,
        target_branch: str = "main",
        verification_receipt: Optional[SelfEvolutionVerificationReceipt] = None,
        approval_id: Optional[str] = None,
        approval_repo: Optional[ApprovalRepository] = None,
        audit_repo: Optional[AuditRepository] = None,
        requested_by: str = "SelfEvolutionEngine",
    ) -> SelfEvolutionPromotionReceipt:
        """
        Executes approval-gated staged promotion into production branch:
        - Fails closed if verification receipt is missing, unverified, or tampered.
        - Fails closed if human approval token is missing or unapproved.
        - Fails closed if any protected core path was modified.
        - Pins pre-evolution commit state.
        - Merges candidate branch self_evolution/{proposal_id} into target_branch.
        - Issues cryptographic SelfEvolutionPromotionReceipt and audit record.
        """
        if not _BRANCH_RE.fullmatch(target_branch) or ".." in target_branch or target_branch.startswith("-"):
            raise PromotionGateError(f"Invalid target branch '{target_branch}'")

        if verification_receipt is None:
            raise PromotionGateError("Promotion failed: verification receipt is required.")
        if verification_receipt.proposal_id != proposal_id:
            raise PromotionGateError("Promotion failed: verification receipt proposal_id mismatch.")
        if not verification_receipt.passed:
            raise PromotionGateError("Promotion failed: verification receipt indicates verification failed.")
        if not verification_receipt.verify_signature():
            raise PromotionGateError("Promotion failed: verification receipt signature is invalid.")

        # 1. STRICT SOVEREIGNTY GATE: REQUIRE VALID APPROVED HUMAN APPROVAL TOKEN
        if not approval_id or approval_repo is None:
            raise HumanApprovalRequiredError(
                action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
                details=f"Promotion of proposal '{proposal_id}' requires an approved human approval token from HumanApprovalGate.",
            )

        approval = await approval_repo.get_by_id(approval_id)
        if approval is None:
            raise HumanApprovalRequiredError(
                action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
                details=f"Human approval token '{approval_id}' was not found in the approval repository.",
            )
        if approval.status != ApprovalStatus.APPROVED:
            raise HumanApprovalRequiredError(
                action_type=DestructiveOperationType.SELF_EVOLUTION_PROMOTION.value,
                details=(
                    f"Human approval token '{approval_id}' is not approved (status='{approval.status.value}'). "
                    f"Production code modifications fail closed without explicit approval."
                ),
            )

        # 2. Verify approval details match proposal and verification receipt
        appr_details = approval.details or {}
        appr_proposal = appr_details.get("proposal_id")
        if appr_proposal and appr_proposal != proposal_id:
            raise PromotionGateError(
                f"Approval '{approval_id}' targets proposal '{appr_proposal}', but promotion requested for '{proposal_id}'."
            )
        appr_verif_id = appr_details.get("verification_receipt_id")
        if appr_verif_id and appr_verif_id != verification_receipt.receipt_id:
            raise PromotionGateError(
                f"Approval '{approval_id}' was granted for verification receipt '{appr_verif_id}', "
                f"but candidate presented receipt '{verification_receipt.receipt_id}'."
            )

        # 3. Verify live candidate worktree matches verification receipt (anti-tamper / anti-drift)
        staging_receipt = self.candidate_manager.verify_candidate_receipt(proposal_id)
        current_candidate_head = self.candidate_manager._worktree_git(
            staging_receipt.path, "rev-parse", "HEAD"
        ).strip().lower()
        if verification_receipt.head_commit and current_candidate_head != verification_receipt.head_commit.lower():
            raise PromotionGateError(
                f"Pre-promotion validation failed: live candidate worktree HEAD '{current_candidate_head}' "
                f"does not match verified commit '{verification_receipt.head_commit.lower()}'. "
                f"Unverified commits were added to candidate branch after multi-gate verification."
            )

        import hashlib
        for rel_path, expected_digest in verification_receipt.artifact_digests.items():
            file_path = staging_receipt.path / rel_path
            if not file_path.is_file():
                raise PromotionGateError(
                    f"Pre-promotion validation failed: verified file '{rel_path}' is missing in candidate worktree."
                )
            actual_digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if actual_digest != expected_digest:
                raise PromotionGateError(
                    f"Pre-promotion validation failed: live candidate file '{rel_path}' has digest '{actual_digest}' "
                    f"differing from verified digest '{expected_digest}'. Code was modified after verification."
                )

        # 4. Check live candidate changed files against Protected Core Files Invariant
        changed_files = self.candidate_manager.list_changed_files(proposal_id)
        for f in changed_files:
            if is_protected_path(f):
                raise ProtectedPathViolationError(
                    f"Candidate modification touches prohibited path '{f}'. "
                    f"Self-evolution modifications to root policy, approval gates, or emergency halt latches are forbidden."
                )

        candidate_branch = self.candidate_manager.get_candidate_branch_name(proposal_id)

        # 4. PIN PRE-EVOLUTION COMMIT STATE
        try:
            pre_evolution_commit = subprocess.run(
                ["git", "-C", str(self.repository), "rev-parse", target_branch],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip().lower()
        except subprocess.SubprocessError as exc:
            raise PromotionGateError(f"Failed to resolve pre-evolution commit for '{target_branch}': {exc}") from exc

        # 5. MERGE CANDIDATE BRANCH INTO TARGET PRODUCTION BRANCH
        current_branch = subprocess.run(
            ["git", "-C", str(self.repository), "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        if current_branch != target_branch:
            subprocess.run(
                ["git", "-C", str(self.repository), "checkout", target_branch],
                check=True,
                capture_output=True,
            )

        merge_msg = f"Promote self-evolution candidate {proposal_id} approved by {approval.decided_by or 'operator'}"
        merge_res = subprocess.run(
            ["git", "-C", str(self.repository), "merge", "--no-ff", candidate_branch, "-m", merge_msg],
            capture_output=True,
            text=True,
        )
        if merge_res.returncode != 0:
            # Abort merge and fail closed
            subprocess.run(["git", "-C", str(self.repository), "merge", "--abort"], check=False)
            raise PromotionGateError(f"Git merge failed during promotion: {merge_res.stderr.strip()}")

        promoted_commit = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().lower()

        # 6. ISSUE TAMPER-EVIDENT PROMOTION RECEIPT
        prom_receipt = SelfEvolutionPromotionReceipt.create(
            proposal_id=proposal_id,
            candidate_branch=candidate_branch,
            target_branch=target_branch,
            pre_evolution_commit=pre_evolution_commit,
            promoted_commit=promoted_commit,
            approval_id=approval_id,
            approved_by=approval.decided_by or "operator",
            verification_receipt_id=verification_receipt.receipt_id,
        )

        # 7. RECORD AUDIT LOG
        if audit_repo:
            await audit_repo.log(
                actor=requested_by,
                action="SELF_EVOLUTION_PROMOTED",
                resource_type="codebase",
                resource_id=proposal_id,
                details={
                    "promotion_receipt_id": prom_receipt.receipt_id,
                    "target_branch": target_branch,
                    "pre_evolution_commit": pre_evolution_commit,
                    "promoted_commit": promoted_commit,
                    "approval_id": approval_id,
                    "approved_by": approval.decided_by,
                },
            )

        logger.info(
            "Promoted self-evolution candidate into production",
            proposal_id=proposal_id,
            target_branch=target_branch,
            pre_evolution_commit=pre_evolution_commit,
            promoted_commit=promoted_commit,
            receipt_id=prom_receipt.receipt_id,
        )
        return prom_receipt
