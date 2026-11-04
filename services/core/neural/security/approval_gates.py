from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from neural.constants import ApprovalStatus, DestructiveOperationType
from neural.db.models import Approval
from neural.db.repositories import ApprovalRepository
from neural.exceptions import HumanApprovalRequiredError
from neural.logger import get_logger

logger = get_logger("ancos.security.approval_gates")


class HumanApprovalGate:
    """
    Human Sovereignty Approval Gate for Destructive Operations.
    Enforces a strict requirement for human operator approval before executing:
    - Destructive file deletions (e.g. workspace purge, artifact deletion)
    - Production deployments (e.g. promoting code to live production cluster)
    - Database migrations (e.g. schema changes, table drops, truncations)
    - Credential rotation (e.g. secret keys, JWT tokens, API keys)
    - Infrastructure destruction / workstation purges
    """

    DESTRUCTIVE_KEYWORDS: set[str] = {
        "drop_table",
        "delete_database",
        "truncate",
        "rm -rf",
        "purge_all",
        "rotate_key",
        "rotate_secret",
        "prod_deploy",
        "deploy_production",
        "destroy_cluster",
        "file_deletion",
        "production_deployment",
        "database_migration",
        "credential_rotation",
        "infrastructure_destroy",
        "recover_orphaned_task",
    }

    @classmethod
    def is_destructive(cls, action_type: str, details: Optional[dict[str, Any]] = None) -> bool:
        """Determines whether an action is classified as destructive and requires approval."""
        norm_action = action_type.strip().lower()

        # 1. Match enum values
        for op in DestructiveOperationType:
            if norm_action == op.value:
                return True

        # 2. Match keyword substrings
        for kw in cls.DESTRUCTIVE_KEYWORDS:
            if kw in norm_action:
                return True

        # 3. Match semantic combinations
        if "rotate" in norm_action and any(w in norm_action for w in ["key", "secret", "credential", "token"]):
            return True
        if "destroy" in norm_action:
            return True
        if "delete" in norm_action and any(w in norm_action for w in ["file", "database", "table", "disk", "all"]):
            return True
        if "deploy" in norm_action and any(w in norm_action for w in ["prod", "production", "live"]):
            return True

        # 4. Check details for destructive flags
        if details:

            if details.get("is_destructive") is True:
                return True
            if details.get("destructive") is True:
                return True
            if str(details.get("environment", "")).lower() == "production" and "deploy" in norm_action:
                return True

        return False

    @classmethod
    async def request_approval(
        cls,
        action_type: str,
        requested_by: str,
        description: str,
        justification: str,
        details: dict[str, Any],
        approval_repo: ApprovalRepository,
        audit_repo: Optional[Any] = None,
    ) -> Approval:
        """Creates a pending human approval request in the database and audit trail."""
        logger.info(
            "Creating human approval request for destructive operation",
            action_type=action_type,
            requested_by=requested_by,
        )

        approval = await approval_repo.create(
            action_type=action_type,
            description=description,
            justification=justification,
            requested_by=requested_by,
            details=details,
            status=ApprovalStatus.PENDING,
        )

        if audit_repo:
            try:
                await audit_repo.log(
                    actor=requested_by,
                    action="APPROVAL_REQUESTED",
                    resource_type="approval",
                    resource_id=approval.id,
                    details={
                        "action_type": action_type,
                        "description": description,
                        "justification": justification,
                    },
                )
            except Exception as e:
                logger.warning("Failed to log approval request in audit repo", error=str(e))

        return approval

    @classmethod
    async def check_authorized(cls, approval_id: str, approval_repo: ApprovalRepository) -> bool:
        """Returns True if the approval request exists and has been APPROVED by an operator."""
        app = await approval_repo.get_by_id(approval_id)
        if not app:
            return False
        return app.status == ApprovalStatus.APPROVED

    @classmethod
    async def decide(
        cls,
        approval_id: str,
        decision: str,
        decided_by: str,
        approval_repo: ApprovalRepository,
        audit_repo: Optional[Any] = None,
    ) -> Optional[Approval]:
        """Records an operator decision (approved/rejected) on an approval request."""
        app = await approval_repo.decide(
            approval_id=approval_id,
            decision=decision,
            decided_by=decided_by,
        )

        if app and audit_repo:
            try:
                await audit_repo.log(
                    actor=decided_by,
                    action="APPROVAL_DECIDED",
                    resource_type="approval",
                    resource_id=approval_id,
                    details={
                        "decision": decision,
                        "status": app.status.value,
                        "decided_by": decided_by,
                    },
                )
            except Exception as e:
                logger.warning("Failed to log approval decision in audit repo", error=str(e))

        return app

    @classmethod
    async def execute_with_gate(
        cls,
        action_type: str,
        requested_by: str,
        description: str,
        operation_callable: Callable[..., Any],
        details: Optional[dict[str, Any]] = None,
        approval_id: Optional[str] = None,
        approval_repo: Optional[ApprovalRepository] = None,
        audit_repo: Optional[Any] = None,
    ) -> Any:
        """
        Executes a protected operation behind the human approval gate.
        If the operation is destructive and not pre-approved, halts execution
        by raising HumanApprovalRequiredError.
        """
        det = details or {}

        if cls.is_destructive(action_type, det):
            if not approval_id:
                if approval_repo:
                    app = await cls.request_approval(
                        action_type=action_type,
                        requested_by=requested_by,
                        description=description,
                        justification=det.get("justification", "Automatic sovereignty gate enforcement"),
                        details=det,
                        approval_repo=approval_repo,
                        audit_repo=audit_repo,
                    )
                    raise HumanApprovalRequiredError(
                        action_type,
                        f"Destructive operation intercepted. Human approval request created: id={app.id}",
                    )
                raise HumanApprovalRequiredError(
                    action_type,
                    "Destructive operation requires human operator approval before execution.",
                )

            # Validate pre-provided approval
            if approval_repo:
                is_auth = await cls.check_authorized(approval_id, approval_repo)
                if not is_auth:
                    raise HumanApprovalRequiredError(
                        action_type,
                        f"Approval request '{approval_id}' is not approved. Execution blocked.",
                    )

        # Execute the authorized operation
        logger.info("Executing authorized gated operation", action_type=action_type, actor=requested_by)
        if inspect.iscoroutinefunction(operation_callable):
            result = await operation_callable()
        else:
            result = operation_callable()
            if inspect.iscoroutine(result):
                result = await result

        if audit_repo:
            try:
                await audit_repo.log(
                    actor=requested_by,
                    action=f"EXECUTED_{action_type.upper()}",
                    resource_type="gated_operation",
                    resource_id=approval_id or "direct",
                    details={"action_type": action_type, "details": det},
                )
            except Exception as e:
                logger.warning("Failed to log executed operation in audit repo", error=str(e))

        return result
