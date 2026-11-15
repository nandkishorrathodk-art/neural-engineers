"""Automated workstation provisioning coordinator.

The company is designed to be autonomous, but workstation provisioning is
gated behind a human-sovereignty approval (``PROVISION_WORKSTATION``). That
gate is intentional: starting a desktop is a resource commitment a human must
sanction. What was missing was the *hand-off* — nothing ever created the
approval request, so an assigned task sat forever waiting for a workstation
that no one had been asked to approve, and every project stalled in
``PLANNING``.

This coordinator closes that gap without bypassing the gate. On each pass it:

1. finds ASSIGNED tasks whose assignee has no live desktop and no in-flight
   provisioning, and raises a *pending* ``PROVISION_WORKSTATION`` approval for
   them (so a human can approve or reject it), and
2. provisions the workstation for any ``PROVISION_WORKSTATION`` approval a human
   has *already approved* and not yet consumed, respecting desktop capacity.

It never approves an approval itself. A human (or the independent approver
role) still has to say yes; this only makes the request visible and acts on the
human's decision.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from neural.constants import ApprovalStatus, EmployeeStatus, TaskStatus
from neural.containers.manager import ContainerError, ContainerManager, DockerBrokerRequestError
from neural.db.engine import AsyncSessionLocal
from neural.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    ResourceLeaseRepository,
    TaskRepository,
)
from neural.logger import get_logger
from neural.resource_scheduler.workstation_lease import claim_workstation_lease
from neural.security.approval_gates import HumanApprovalGate

logger = get_logger("ancos.tasks.workstation_provisioning")

_ACTION = "PROVISION_WORKSTATION"
_REQUESTED_BY = "BossAI"


def provisioning_reconcile_interval_seconds() -> int:
    raw = os.environ.get("ANCOS_PROVISIONING_RECONCILE_SECONDS", "15")
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ValueError(
            "ANCOS_PROVISIONING_RECONCILE_SECONDS must be an integer from 5 to 3600."
        ) from exc
    if not 5 <= interval <= 3600:
        raise ValueError(
            "ANCOS_PROVISIONING_RECONCILE_SECONDS must be an integer from 5 to 3600."
        )
    return interval


def _open_provision_employee_ids(approvals: list[Any]) -> set[str]:
    """Employee ids with a pending/approved, unconsumed provision approval.

    Prevents piling up duplicate requests for the same employee across passes.
    """
    ids: set[str] = set()
    for approval in approvals:
        if approval.action_type != _ACTION or approval.consumed_at is not None:
            continue
        if approval.status not in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
            continue
        employee_id = (approval.details or {}).get("employee_id")
        if employee_id:
            ids.add(employee_id)
    return ids


async def _has_running_desktop(
    employee_id: str,
    container_mgr: ContainerManager,
    container_repo: ContainerRepository,
    employee_repo: EmployeeRepository,
) -> bool:
    workstation = await container_mgr.get_desktop_by_employee_id(
        employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    return bool(workstation) and workstation.get("status") == "running"


async def request_pending_workstation_approvals(
    task_repo: TaskRepository,
    employee_repo: EmployeeRepository,
    approval_repo: ApprovalRepository,
    audit_repo: AuditRepository,
    lease_repo: ResourceLeaseRepository,
    container_repo: ContainerRepository,
    container_mgr: ContainerManager,
) -> list[str]:
    """Raise a pending PROVISION_WORKSTATION approval for each unprovisioned assignee."""
    requested: list[str] = []
    open_ids = _open_provision_employee_ids(list(await approval_repo.list_all()))
    assigned = await task_repo.list_by_status(TaskStatus.ASSIGNED)

    seen: set[str] = set()
    for task in assigned:
        # Snapshot the ORM values we need up front: the calls below may commit,
        # and lease operations can roll the shared session back (expiring ORM
        # objects), so attribute access afterwards would raise MissingGreenlet.
        assignee_id = task.assigned_to
        if not assignee_id:
            continue
        employee = await employee_repo.get_by_employee_id(assignee_id)
        if not employee:
            continue
        employee_id = employee.employee_id
        employee_status = employee.status
        internal_id = employee.id
        task_title = task.title
        task_id = task.task_id
        if employee_id in seen:
            continue
        seen.add(employee_id)

        if employee_status in (EmployeeStatus.SUSPENDED, EmployeeStatus.TERMINATED):
            continue

        # An active lease means the workstation is live or provisioning is already
        # in flight; do not ask again.
        blocking = await lease_repo.list_blocking_for_employee(
            employee_id=employee_id,
            internal_employee_id=internal_id,
        )
        if any(lease.get("status") == "active" for lease in blocking):
            continue
        if employee_id in open_ids:
            continue
        if await _has_running_desktop(employee_id, container_mgr, container_repo, employee_repo):
            continue

        approval = await HumanApprovalGate.request_approval(
            action_type=_ACTION,
            requested_by=_REQUESTED_BY,
            description=f"{_ACTION} for employee {employee_id}",
            justification=(
                f"Task '{task_title}' is assigned to {employee_id} "
                f"but no live workstation exists. Approve to provision a desktop "
                f"so the employee can execute the work."
            ),
            details={"employee_id": employee_id},
            approval_repo=approval_repo,
            audit_repo=audit_repo,
        )
        open_ids.add(employee_id)
        requested.append(employee_id)
        logger.info(
            "Requested workstation provisioning approval",
            employee_id=employee_id,
            approval_id=approval.id,
            task_id=task_id,
        )
    return requested


async def provision_approved_workstations(
    approval_repo: ApprovalRepository,
    employee_repo: EmployeeRepository,
    container_repo: ContainerRepository,
    lease_repo: ResourceLeaseRepository,
    task_repo: TaskRepository,
    container_mgr: ContainerManager,
) -> list[str]:
    """Provision workstations for approvals a human has approved and not consumed."""
    provisioned: list[str] = []
    # Snapshot candidate approvals into primitives before touching any lease: a
    # lease claim can roll the shared session back, which expires ORM objects.
    candidates = [
        {
            "approval_id": approval.id,
            "employee_id": (approval.details or {}).get("employee_id"),
        }
        for approval in await approval_repo.list_all()
        if approval.action_type == _ACTION
        and approval.status == ApprovalStatus.APPROVED
        and approval.consumed_at is None
        and (approval.details or {}).get("employee_id")
    ]

    for candidate in candidates:
        approval_id = candidate["approval_id"]
        requested_employee_id = candidate["employee_id"]
        employee = await employee_repo.get_by_employee_id(requested_employee_id)
        if not employee:
            continue
        employee_id = employee.employee_id
        docker_id = f"ancos-desktop-{employee_id.lower()}"

        lease = await claim_workstation_lease(lease_repo, employee_id, docker_id)
        if lease is None:
            # No free desktop slot this pass; leave the approval for a later cycle.
            logger.info(
                "Deferring approved provisioning: no free workstation slot",
                employee_id=employee_id,
                approval_id=approval_id,
            )
            continue

        consumed = await approval_repo.consume_once(
            approval_id,
            _ACTION,
            {"employee_id": employee_id},
            _REQUESTED_BY,
        )
        if not consumed:
            await lease_repo.release(lease["lease_id"], lease["fencing_token"])
            continue

        try:
            spec = await container_mgr.provision_employee_desktop(
                employee_id=employee_id,
                role_image="employee-base",
                container_repo=container_repo,
                employee_repo=employee_repo,
            )
        except (DockerBrokerRequestError, ContainerError) as exc:
            # Release the slot only if the broker confirms no workstation was
            # created; otherwise keep the lease for TTL quarantine and reaping.
            try:
                await container_mgr._broker_request("GET", f"/v1/containers/{docker_id}")
            except DockerBrokerRequestError as inspect_error:
                if inspect_error.status_code == 404:
                    await lease_repo.release(lease["lease_id"], lease["fencing_token"])
            except Exception:
                pass
            logger.warning(
                "Approved workstation provisioning failed",
                employee_id=employee_id,
                approval_id=approval_id,
                error=str(exc),
            )
            continue

        provisioned.append(spec["docker_id"])
        logger.info(
            "Provisioned approved workstation",
            employee_id=employee_id,
            docker_id=spec["docker_id"],
            approval_id=approval_id,
        )

    if provisioned:
        # A workstation that just came up may unblock PENDING tasks that were
        # waiting on it; re-dispatch so they do not wait for the next poll.
        try:
            await task_repo.redispatch_pending_tasks()
        except Exception as exc:  # a dispatch failure must not undo a good provision
            logger.warning("Post-provision redispatch failed", error=str(exc))
    return provisioned


async def run_provisioning_reconcile_cycle(container_mgr: ContainerManager) -> dict[str, list[str]]:
    """One full pass: request approvals for unprovisioned assignees, provision approved."""
    async with AsyncSessionLocal() as session:
        task_repo = TaskRepository(session)
        employee_repo = EmployeeRepository(session)
        approval_repo = ApprovalRepository(session)
        audit_repo = AuditRepository(session)
        lease_repo = ResourceLeaseRepository(session)
        container_repo = ContainerRepository(session)

        requested = await request_pending_workstation_approvals(
            task_repo,
            employee_repo,
            approval_repo,
            audit_repo,
            lease_repo,
            container_repo,
            container_mgr,
        )
        provisioned = await provision_approved_workstations(
            approval_repo,
            employee_repo,
            container_repo,
            lease_repo,
            task_repo,
            container_mgr,
        )
    return {"requested": requested, "provisioned": provisioned}


async def workstation_provisioning_loop(
    container_mgr: ContainerManager,
    interval_seconds: Optional[int] = None,
) -> None:
    """Continuously reconcile workstation provisioning against assigned tasks."""
    interval = interval_seconds or provisioning_reconcile_interval_seconds()
    while True:
        try:
            result = await run_provisioning_reconcile_cycle(container_mgr)
            if result["requested"] or result["provisioned"]:
                logger.info(
                    "Workstation provisioning reconcile complete",
                    requested=result["requested"],
                    provisioned=result["provisioned"],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a transient DB/broker error must not kill the loop
            logger.warning("Workstation provisioning reconcile cycle failed", error=str(exc))
        await asyncio.sleep(interval)
