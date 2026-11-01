from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from neural.api.deps import get_db_session, require_owner_identity
from neural.db.repositories import ApprovalRepository, AuditRepository, TaskRepository
from neural.security.approval_gates import HumanApprovalGate
from neural.security.kill_switch import get_kill_switch
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/security", tags=["Zero-Trust Security & Control Room"])


class EmergencyStopRequest(BaseModel):
    reason: str = Field(default="Operator triggered emergency stop via Control Room")


class ResumeRequest(BaseModel):
    recovery_strategy: str = Field(default="checkpoint")


class ApprovalDecisionPayload(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    notes: Optional[str] = None


class ApprovalRequestPayload(BaseModel):
    action_type: str = Field(pattern="^(PROVISION_WORKSTATION|ROTATE_VNC_PASSWORD|TERMINATE_WORKSTATION)$")
    employee_id: str = Field(min_length=1, max_length=128)
    docker_id: Optional[str] = Field(default=None, max_length=128)
    justification: str = Field(min_length=8, max_length=2000)


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
    actor: str = Depends(require_owner_identity),
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
        actor=actor,
        audit_repo=audit_repo,
    )
    return receipt


@router.post("/resume")
async def resume_system(
    body: ResumeRequest,
    session: AsyncSession = Depends(get_db_session),
    actor: str = Depends(require_owner_identity),
) -> dict[str, Any]:
    """
    Resumes system operations from an emergency halt.
    Restores interrupted tasks to PENDING state and unpauses schedulers.
    """
    kill_switch = get_kill_switch()
    task_repo = TaskRepository(session)
    audit_repo = AuditRepository(session)
    receipt = await kill_switch.resume(
        actor=actor,
        recovery_strategy=body.recovery_strategy,
        auth_token=None,
        task_repo=task_repo,
        audit_repo=audit_repo,
    )
    return receipt


@router.get("/approvals")
async def list_pending_approvals(
    session: AsyncSession = Depends(get_db_session),
    _: str = Depends(require_owner_identity),
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


@router.post("/approvals")
async def request_workstation_approval(
    body: ApprovalRequestPayload,
    session: AsyncSession = Depends(get_db_session),
    actor: str = Depends(require_owner_identity),
) -> dict[str, Any]:
    needs_container = body.action_type in {"ROTATE_VNC_PASSWORD", "TERMINATE_WORKSTATION"}
    if needs_container != bool(body.docker_id):
        raise HTTPException(
            status_code=422,
            detail="This action requires docker_id for scope, while provisioning approvals must omit it.",
        )
    approval_repo = ApprovalRepository(session)
    audit_repo = AuditRepository(session)
    details = {"employee_id": body.employee_id}
    if body.docker_id:
        details["docker_id"] = body.docker_id
    approval = await HumanApprovalGate.request_approval(
        action_type=body.action_type,
        requested_by=actor,
        description=f"{body.action_type} for employee {body.employee_id}",
        justification=body.justification,
        details=details,
        approval_repo=approval_repo,
        audit_repo=audit_repo,
    )
    return {"id": approval.id, "action_type": approval.action_type, "status": approval.status.value, "details": approval.details}


@router.post("/approvals/{approval_id}/decide")
async def decide_approval_request(
    approval_id: str,
    body: ApprovalDecisionPayload,
    session: AsyncSession = Depends(get_db_session),
    actor: str = Depends(require_owner_identity),
) -> dict[str, Any]:
    """Decides a human approval request ('approved' or 'rejected')."""
    approval_repo = ApprovalRepository(session)
    audit_repo = AuditRepository(session)

    app = await HumanApprovalGate.decide(
        approval_id=approval_id,
        decision=body.decision,
        decided_by=actor,
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
