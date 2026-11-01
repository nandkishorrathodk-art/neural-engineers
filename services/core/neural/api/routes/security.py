from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from neural.api.deps import get_db_session
from neural.db.repositories import ApprovalRepository, AuditRepository, TaskRepository
from neural.security.approval_gates import HumanApprovalGate
from neural.security.kill_switch import get_kill_switch
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/security", tags=["Zero-Trust Security & Control Room"])


class EmergencyStopRequest(BaseModel):
    reason: str = Field(default="Operator triggered emergency stop via Control Room")
    actor: str = Field(default="human_operator")


class ResumeRequest(BaseModel):
    actor: str = Field(default="human_operator")
    recovery_strategy: str = Field(default="checkpoint")
    auth_token: Optional[str] = None


class ApprovalDecisionPayload(BaseModel):
    decision: str = Field(description="Must be 'approved' or 'rejected'")
    decided_by: str = Field(default="human_operator")
    notes: Optional[str] = None


@router.get("/status")
async def get_security_status() -> dict[str, Any]:
    """Retrieves current Zero-Trust security and emergency kill switch status."""
    kill_switch = get_kill_switch()
    return {
        "kill_switch_active": kill_switch.is_halted,
        "halt_reason": kill_switch.halt_reason,
        "halted_by": kill_switch.halted_by,
        "halt_timestamp": kill_switch.halt_timestamp.isoformat() if kill_switch.halt_timestamp else None,
        "halt_latency_ms": kill_switch.halt_latency_ms,
        "seccomp_profile": "SCMP_ACT_ERRNO",
        "apparmor_profile": "ancos-workstation",
        "network_perimeter": "active",
        "cloud_metadata_blocked": True,
    }


@router.post("/emergency-stop")
async def trigger_emergency_stop(
    body: EmergencyStopRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Activates the Global Emergency Kill Switch.
    Immediately freezes all autonomous worker loops, active DAG dispatchers,
    containerized desktop workstations, and the Boss AI scheduler in < 200ms.
    """
    kill_switch = get_kill_switch()
    audit_repo = AuditRepository(session)
    receipt = await kill_switch.emergency_halt(
        reason=body.reason,
        actor=body.actor,
        audit_repo=audit_repo,
    )
    return receipt


@router.post("/resume")
async def resume_system(
    body: ResumeRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Resumes system operations from an emergency halt.
    Restores interrupted tasks to PENDING state and unpauses schedulers.
    """
    kill_switch = get_kill_switch()
    task_repo = TaskRepository(session)
    audit_repo = AuditRepository(session)
    receipt = await kill_switch.resume(
        actor=body.actor,
        recovery_strategy=body.recovery_strategy,
        auth_token=body.auth_token,
        task_repo=task_repo,
        audit_repo=audit_repo,
    )
    return receipt


@router.get("/approvals")
async def list_pending_approvals(
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Returns all pending human sovereignty approval requests for destructive operations."""
    approval_repo = ApprovalRepository(session)
    pending = await approval_repo.list_pending()
    return [
        {
            "id": a.id,
            "action_type": a.action_type,
            "description": a.description,
            "justification": a.justification,
            "status": a.status.value,
            "requested_by": a.requested_by,
            "details": a.details,
            "requested_at": a.requested_at.isoformat() if a.requested_at else None,
        }
        for a in pending
    ]


@router.post("/approvals/{approval_id}/decide")
async def decide_approval_request(
    approval_id: str,
    body: ApprovalDecisionPayload,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Decides a human approval request ('approved' or 'rejected')."""
    approval_repo = ApprovalRepository(session)
    audit_repo = AuditRepository(session)

    app = await HumanApprovalGate.decide(
        approval_id=approval_id,
        decision=body.decision,
        decided_by=body.decided_by,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
    )

    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request '{approval_id}' not found",
        )

    return {
        "id": app.id,
        "action_type": app.action_type,
        "status": app.status.value,
        "decided_by": app.decided_by,
        "decided_at": app.decided_at.isoformat() if app.decided_at else None,
    }
