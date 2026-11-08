from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from neural.cognitive_fabric.memory.models import valid_independent_verification_receipt
from neural.constants import (
    ActionItemStatus,
    ApprovalStatus,
    ContainerStatus,
    EmployeeStatus,
    MeetingStatus,
    MemoryCategory,
    ProposalStatus,
    ProposalType,
    SkillLevel,
    TaskPriority,
    TaskStatus,
    VerificationStatus,
    VoteChoice,
)
from neural.db.models import (
    ActionItem,
    Approval,
    Artifact,
    AuditLog,
    BudgetAccount,
    BudgetReservation,
    Container,
    DecisionLedgerEntry,
    DecisionRecord,
    EmergencyStopState,
    Employee,
    EmployeeSkill,
    EvolutionRecord,
    Meeting,
    MemoryEntry,
    Message,
    MethodBenchmarkRecord,
    MethodRecord,
    OutboxEvent,
    PerformanceRecord,
    Project,
    Proposal,
    ResourceLease,
    ServiceHeartbeat,
    SkillCertification,
    Task,
    TaskAttempt,
    TaskDependency,
    TaskResult,
    ToolRecord,
    Vote,
)
from neural.exceptions import UnverifiedTaskError, VerificationFailedError


def _task_dispatch_order() -> tuple[Any, ...]:
    priority_rank = case(
        (Task.priority == TaskPriority.CRITICAL, 0),
        (Task.priority == TaskPriority.HIGH, 1),
        (Task.priority == TaskPriority.MEDIUM, 2),
        (Task.priority == TaskPriority.LOW, 3),
        else_=4,
    )
    return priority_rank.asc(), Task.deadline.asc().nulls_last(), Task.task_id.asc()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_session_lock(session: AsyncSession) -> asyncio.Lock:
    if not hasattr(session, "_neural_lock"):
        session._neural_lock = asyncio.Lock()
    return session._neural_lock


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, objective: str, execution_plan: Optional[dict[str, Any]] = None) -> Project:
        project = Project(name=name, objective=objective, execution_plan=execution_plan)
        self.session.add(project)
        await self.session.commit()
        await self.session.refresh(project)
        return project

    async def get_by_id(self, project_id: str) -> Optional[Project]:
        stmt = (
            select(Project)
            .where(Project.id == project_id)
            .options(selectinload(Project.tasks))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self) -> Sequence[Project]:
        stmt = (
            select(Project)
            .options(selectinload(Project.tasks))
            .order_by(Project.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class BudgetRepository:
    """Atomically reserves and settles shared USD quotas across scopes."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _money(value: int | float | Decimal, field: str) -> Decimal:
        if isinstance(value, bool):
            raise ValueError(f"{field} must be a finite USD amount")
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field} must be a finite USD amount") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError(f"{field} must be a finite non-negative USD amount")
        try:
            normalized = amount.quantize(Decimal("0.00000001"))
        except InvalidOperation as exc:
            raise ValueError(f"{field} exceeds supported USD precision") from exc
        if normalized > Decimal("9999999999.99999999"):
            raise ValueError(f"{field} exceeds supported USD range")
        return normalized

    async def reserve(
        self,
        *,
        idempotency_key: str,
        reserved_usd: int | float | Decimal,
        scope_caps: dict[str, int | float | Decimal],
    ) -> BudgetReservation:
        """Reserve one call against every provided cap in a single DB transaction.

        `scope_caps` should include each applicable organization-month, project,
        and task account. The caller supplies the maximum cost of this provider
        attempt, not a predicted average.
        """
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key must contain 1-128 characters")
        amount = self._money(reserved_usd, "reserved_usd")
        if amount <= 0:
            raise ValueError("reserved_usd must be greater than zero")
        if not scope_caps:
            raise ValueError("at least one budget scope is required")
        caps = {key: self._money(cap, "scope cap") for key, cap in scope_caps.items()}
        if any(not key or len(key) > 192 for key in caps):
            raise ValueError("budget scope keys must contain 1-192 characters")
        if len(caps) != len(scope_caps):
            raise ValueError("budget scope keys must be unique")

        dialect = self.session.get_bind().dialect.name
        insert_fn = {"sqlite": sqlite_insert, "postgresql": pg_insert}.get(dialect)
        if insert_fn is None:
            raise RuntimeError(f"Budget reservations do not support database dialect {dialect!r}")

        reservation_id = str(uuid.uuid4())
        reservation_stmt = (
            insert_fn(BudgetReservation)
            .values(
                id=reservation_id,
                idempotency_key=idempotency_key,
                reserved_usd=amount,
                scope_keys=sorted(caps),
                scope_caps={key: str(value) for key, value in caps.items()},
                status="reserved",
                created_at=utc_now(),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(BudgetReservation.id)
        )
        try:
            inserted_id = await self.session.scalar(reservation_stmt)
            if inserted_id is None:
                existing = await self.session.scalar(
                    select(BudgetReservation).where(BudgetReservation.idempotency_key == idempotency_key)
                )
                if existing is None:
                    raise RuntimeError("Reservation conflict occurred but its row could not be read")
                if (
                    existing.reserved_usd != amount
                    or set(existing.scope_keys) != set(caps)
                    or existing.scope_caps != {key: str(value) for key, value in caps.items()}
                ):
                    raise ValueError("idempotency_key was already used for a different reservation")
                return existing

            for scope_key, cap in sorted(caps.items()):
                account_insert = insert_fn(BudgetAccount).values(
                    scope_key=scope_key,
                    cap_usd=cap,
                    reserved_usd=Decimal("0"),
                    spent_usd=Decimal("0"),
                    updated_at=utc_now(),
                ).on_conflict_do_nothing(index_elements=["scope_key"])
                await self.session.execute(account_insert)
                updated = await self.session.execute(
                    update(BudgetAccount)
                    .where(
                        BudgetAccount.scope_key == scope_key,
                        BudgetAccount.cap_usd == cap,
                        BudgetAccount.reserved_usd + BudgetAccount.spent_usd + amount <= cap,
                    )
                    .values(
                        reserved_usd=BudgetAccount.reserved_usd + amount,
                        updated_at=utc_now(),
                    )
                )
                if updated.rowcount != 1:
                    account = await self.session.get(BudgetAccount, scope_key)
                    if account is None or account.cap_usd != cap:
                        raise ValueError(f"Budget cap for scope {scope_key!r} changed")
                    from neural.exceptions import QuotaExceededError

                    raise QuotaExceededError(f"Budget scope {scope_key!r} cannot reserve ${amount}")

            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        reservation = await self.session.get(BudgetReservation, reservation_id)
        if reservation is None:
            raise RuntimeError("Reserved budget row disappeared after commit")
        return reservation

    async def settle(
        self,
        reservation_id: str,
        *,
        actual_usd: int | float | Decimal,
        usage_receipt: dict[str, Any],
    ) -> BudgetReservation:
        """Convert a reservation to spend; retain overrun evidence if estimates fail."""
        actual = self._money(actual_usd, "actual_usd")
        reservation = await self.session.scalar(
            select(BudgetReservation)
            .where(BudgetReservation.id == reservation_id)
        )
        if reservation is None:
            raise ValueError("budget reservation not found")
        if reservation.status in {"settled", "overrun"}:
            if reservation.actual_usd != actual:
                raise ValueError("reservation was already settled with different usage")
            return reservation
        if reservation.status != "reserved":
            raise ValueError(f"cannot settle reservation in state {reservation.status!r}")

        claimed = await self.session.execute(
            update(BudgetReservation)
            .where(BudgetReservation.id == reservation_id, BudgetReservation.status == "reserved")
            .values(status="settling")
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise ValueError("budget reservation is already being settled")

        for scope_key in sorted(reservation.scope_keys):
            updated = await self.session.execute(
                update(BudgetAccount)
                .where(
                    BudgetAccount.scope_key == scope_key,
                    BudgetAccount.reserved_usd >= reservation.reserved_usd,
                )
                .values(
                    reserved_usd=BudgetAccount.reserved_usd - reservation.reserved_usd,
                    spent_usd=BudgetAccount.spent_usd + actual,
                    updated_at=utc_now(),
                )
            )
            if updated.rowcount != 1:
                await self.session.rollback()
                raise RuntimeError(f"Reserved amount is missing from budget scope {scope_key!r}")
        reservation.actual_usd = actual
        reservation.usage_receipt = usage_receipt
        await self.session.execute(
            update(BudgetReservation)
            .where(BudgetReservation.id == reservation_id, BudgetReservation.status == "settling")
            .values(
                status="overrun" if actual > reservation.reserved_usd else "settled",
                actual_usd=actual,
                usage_receipt=usage_receipt,
                settled_at=utc_now(),
            )
        )
        await self.session.commit()
        await self.session.refresh(reservation)
        return reservation

    async def release_after_confirmed_no_charge(
        self,
        reservation_id: str,
        *,
        provider_confirmed_unbilled: bool,
        reason: str,
    ) -> BudgetReservation:
        """Release only when provider evidence confirms the request incurred no charge."""
        if provider_confirmed_unbilled is not True:
            raise ValueError("ambiguous provider outcomes must remain reserved")
        if not reason.strip() or len(reason) > 2000:
            raise ValueError("release reason must contain 1-2000 characters")
        reservation = await self.session.scalar(
            select(BudgetReservation)
            .where(BudgetReservation.id == reservation_id)
        )
        if reservation is None:
            raise ValueError("budget reservation not found")
        if reservation.status == "released":
            return reservation
        if reservation.status != "reserved":
            raise ValueError(f"cannot release reservation in state {reservation.status!r}")
        claimed = await self.session.execute(
            update(BudgetReservation)
            .where(BudgetReservation.id == reservation_id, BudgetReservation.status == "reserved")
            .values(status="releasing")
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise ValueError("budget reservation is already being released")
        for scope_key in sorted(reservation.scope_keys):
            updated = await self.session.execute(
                update(BudgetAccount)
                .where(
                    BudgetAccount.scope_key == scope_key,
                    BudgetAccount.reserved_usd >= reservation.reserved_usd,
                )
                .values(
                    reserved_usd=BudgetAccount.reserved_usd - reservation.reserved_usd,
                    updated_at=utc_now(),
                )
            )
            if updated.rowcount != 1:
                await self.session.rollback()
                raise RuntimeError(f"Reserved amount is missing from budget scope {scope_key!r}")
        await self.session.execute(
            update(BudgetReservation)
            .where(BudgetReservation.id == reservation_id, BudgetReservation.status == "releasing")
            .values(status="released", release_reason=reason.strip(), settled_at=utc_now())
        )
        await self.session.commit()
        await self.session.refresh(reservation)
        return reservation



class TaskRepository:
    def __init__(self, session: AsyncSession, strict_verification: Optional[bool] = None):
        self.session = session
        # Completion is an evidence invariant in every environment. Keep the
        # argument for call-site compatibility, but never let configuration
        # turn an unverified task into a successful one.
        self.strict_verification = True

    async def create(self, **kwargs: Any) -> Task:
        task = Task(**kwargs)
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def _get_task_for_worker(self, task_id: str) -> Optional[Task]:
        stmt = (
            select(Task)
            .where((Task.task_id == task_id) | (Task.id == task_id))
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def claim_next_task(
        self,
        worker_id: str,
        lease_seconds: int = 60,
        execution_modes: Optional[Sequence[Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Atomically lease one assigned task and issue its monotonic fencing token."""
        now = utc_now()
        candidate_query = select(Task).where(
            Task.assigned_to.is_not(None),
            Task.status.in_([TaskStatus.ASSIGNED, TaskStatus.RUNNING]),
        )
        if execution_modes is not None:
            mode_values = [getattr(mode, "value", mode) for mode in execution_modes]
            candidate_query = candidate_query.where(Task.execution_mode.in_(mode_values))
        candidates = (
            await self.session.execute(
                candidate_query.order_by(*_task_dispatch_order())
                .limit(64)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

        for task in candidates:
            dependency_evidence = (
                await self.session.execute(
                    select(Task.status, Task.verification_result)
                    .join(TaskDependency, TaskDependency.depends_on_task_id == Task.id)
                    .where(TaskDependency.task_id == task.id)
                )
            ).all()
            if any(
                status != TaskStatus.COMPLETED
                or not isinstance(receipt, dict)
                or receipt.get("passed") is not True
                for status, receipt in dependency_evidence
            ):
                continue

            attempts = (
                await self.session.execute(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_id == task.id)
                    .order_by(TaskAttempt.attempt_number.desc())
                )
            ).scalars().all()
            latest_attempt = attempts[0] if attempts else None
            if latest_attempt and latest_attempt.status in {"leased", "running"} and latest_attempt.lease_expires_at <= now:
                latest_attempt.status = "failed"
                latest_attempt.finished_at = now
                latest_attempt.error_message = "Worker lease expired before the attempt completed."
                if task.retry_count >= task.max_retries:
                    task.status = TaskStatus.FAILED
                    task.completed_at = now
                    task.fencing_token += 1
                    employee = await self.session.get(Employee, task.assigned_to)
                    if employee:
                        employee.status = EmployeeStatus.IDLE
                    await self.session.commit()
                    continue

                retry_delay_seconds = min(2 ** task.retry_count, 60)
                latest_attempt.lease_expires_at = now + timedelta(seconds=retry_delay_seconds)
                task.status = TaskStatus.ASSIGNED
                task.retry_count += 1
                task.fencing_token += 1
                await self.session.commit()
                continue
            if latest_attempt and latest_attempt.status == "submitted":
                # A submitted attempt waits for independent verification.
                continue
            if latest_attempt and latest_attempt.status in {"failed", "rejected"} and latest_attempt.lease_expires_at > now:
                # Persisted retry backoff survives worker process restarts.
                continue
            if latest_attempt and latest_attempt.status == "waiting" and latest_attempt.lease_expires_at > now:
                continue
            employee = await self.session.scalar(
                select(Employee)
                .options(selectinload(Employee.container))
                .execution_options(populate_existing=True)
                .where(
                    Employee.id == task.assigned_to,
                )
            )
            if not employee or not employee.container or employee.container.status != ContainerStatus.RUNNING:
                continue
            retrying_same_assignment = bool(
                latest_attempt and latest_attempt.status in {"failed", "rejected", "expired"}
            )
            if employee.status != EmployeeStatus.IDLE and not (
                employee.status == EmployeeStatus.WORKING and retrying_same_assignment
            ):
                continue
            other_active_attempt = await self.session.scalar(
                select(TaskAttempt.id)
                .join(Task, Task.id == TaskAttempt.task_id)
                .where(
                    Task.assigned_to == employee.id,
                    Task.id != task.id,
                    TaskAttempt.status.in_(["leased", "running"]),
                    TaskAttempt.lease_expires_at > now,
                )
                .limit(1)
            )
            if other_active_attempt:
                continue
            if latest_attempt and latest_attempt.status in {"leased", "running"}:
                if latest_attempt.fencing_token == task.fencing_token and latest_attempt.lease_expires_at > now:
                    continue
                latest_attempt.status = "expired"
                latest_attempt.finished_at = now
                latest_attempt.error_message = "Worker lease expired or was revoked by a newer fencing token."

            new_fencing_token = task.fencing_token + 1
            attempt_number = (latest_attempt.attempt_number + 1) if latest_attempt else 1
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            async with get_session_lock(self.session):
                claimed = await self.session.execute(
                    update(Task)
                    .where(
                        Task.id == task.id,
                        Task.fencing_token == task.fencing_token,
                        Task.status == task.status,
                    )
                    .values(fencing_token=new_fencing_token)
                )
                if claimed.rowcount != 1:
                    await self.session.rollback()
                    continue
                employee_claim = await self.session.execute(
                    update(Employee)
                    .where(Employee.id == employee.id, Employee.status == employee.status)
                    .values(status=EmployeeStatus.WORKING)
                )
                if employee_claim.rowcount != 1:
                    await self.session.rollback()
                    continue
                attempt = TaskAttempt(
                    task_id=task.id,
                    attempt_number=attempt_number,
                    worker_id=worker_id,
                    fencing_token=new_fencing_token,
                    status="leased",
                    claimed_at=now,
                    lease_expires_at=lease_expires_at,
                )
                self.session.add(attempt)
                await self.session.commit()

            return {
                "task_id": task.task_id,
                "project_id": task.project_id,
                "employee_id": employee.employee_id,
                "workstation_container_id": employee.container.docker_id,
                "title": task.title,
                "objective": task.objective,
                "scope": task.scope,
                "acceptance_criteria": task.acceptance_criteria,
                "execution_mode": task.execution_mode.value,
                "resource_budget": task.resource_budget or {},
                "attempt_id": attempt.id,
                "attempt_number": attempt_number,
                "fencing_token": new_fencing_token,
                "lease_expires_at": lease_expires_at.isoformat(),
                "status": task.status.value,
            }

        # Owner objective submission assigns the first task. Once it receives an
        # independent passing receipt, later DAG tasks must be dispatched to a
        # currently available employee instead of remaining pending forever.
        # Only employees with an already-running desktop are eligible here: this
        # path never provisions a workstation or crosses the approval boundary.
        ready_query = select(Task).where(
            Task.status == TaskStatus.PENDING,
            Task.assigned_to.is_(None),
        )
        if execution_modes is not None:
            mode_values = [getattr(mode, "value", mode) for mode in execution_modes]
            ready_query = ready_query.where(Task.execution_mode.in_(mode_values))
        ready_tasks = (
            await self.session.execute(
                ready_query.order_by(*_task_dispatch_order())
                .limit(64)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        if ready_tasks:
            eligible_employees = (
                await self.session.execute(
                    select(Employee)
                    .join(Container, Employee.container_id == Container.id)
                    .options(selectinload(Employee.skills), selectinload(Employee.container))
                    .where(
                        Employee.status == EmployeeStatus.IDLE,
                        Container.status == ContainerStatus.RUNNING,
                    )
                    .order_by(Employee.hired_at.asc())
                    .execution_options(populate_existing=True)
                )
            ).scalars().all()
            from neural.boss.delegator import TaskDelegator

            delegator = TaskDelegator()
            for ready_task in ready_tasks:
                dependency_evidence = (
                    await self.session.execute(
                        select(Task.status, Task.verification_result)
                        .join(TaskDependency, TaskDependency.depends_on_task_id == Task.id)
                        .where(TaskDependency.task_id == ready_task.id)
                    )
                ).all()
                if any(
                    status != TaskStatus.COMPLETED
                    or not isinstance(receipt, dict)
                    or receipt.get("passed") is not True
                    for status, receipt in dependency_evidence
                ):
                    continue
                assignee = delegator.select_best_assignee(ready_task, eligible_employees)
                if not assignee:
                    continue
                assigned = await self.session.execute(
                    update(Task)
                    .where(
                        Task.id == ready_task.id,
                        Task.status == TaskStatus.PENDING,
                        Task.assigned_to.is_(None),
                    )
                    .values(
                        assigned_to=assignee.id,
                        status=TaskStatus.ASSIGNED,
                        fencing_token=Task.fencing_token + 1,
                    )
                )
                if assigned.rowcount == 1:
                    await self.session.commit()
                    return await self.claim_next_task(
                        worker_id,
                        lease_seconds=lease_seconds,
                        execution_modes=execution_modes,
                    )
                await self.session.rollback()
        return None

    async def worker_claim_is_current(self, task_id: str, worker_id: str, fencing_token: int) -> bool:
        """Check that this worker still owns a live, not-yet-started attempt."""
        now = utc_now()
        task = await self._get_task_for_worker(task_id)
        if (
            not task
            or task.fencing_token != fencing_token
            or task.status not in {TaskStatus.ASSIGNED, TaskStatus.RUNNING}
        ):
            return False
        attempt_id = await self.session.scalar(
            select(TaskAttempt.id).where(
                TaskAttempt.task_id == task.id,
                TaskAttempt.worker_id == worker_id,
                TaskAttempt.fencing_token == fencing_token,
                TaskAttempt.status == "leased",
                TaskAttempt.lease_expires_at > now,
            )
        )
        return attempt_id is not None

    async def start_task_attempt(
        self,
        task_id: str,
        worker_id: str,
        fencing_token: int,
        workspace_manifest_before: dict[str, Any],
    ) -> bool:
        now = utc_now()
        task = await self._get_task_for_worker(task_id)
        if not task or task.fencing_token != fencing_token:
            return False
        async with get_session_lock(self.session):
            attempt_update = await self.session.execute(
                update(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.fencing_token == fencing_token,
                    TaskAttempt.status == "leased",
                    TaskAttempt.lease_expires_at > now,
                )
                .values(
                    status="running",
                    started_at=now,
                    heartbeat_at=now,
                    workspace_manifest_before=workspace_manifest_before,
                )
            )
            if attempt_update.rowcount != 1:
                await self.session.rollback()
                return False
            task_update = await self.session.execute(
                update(Task)
                .where(
                    Task.id == task.id,
                    Task.fencing_token == fencing_token,
                    Task.status.in_([TaskStatus.ASSIGNED, TaskStatus.RUNNING]),
                )
                .values(status=TaskStatus.RUNNING, started_at=now)
            )
            if task_update.rowcount != 1:
                await self.session.rollback()
                return False
            await self.session.commit()
        return True

    async def worker_lease_is_current(self, task_id: str, worker_id: str, fencing_token: int) -> bool:
        now = utc_now()
        task = await self._get_task_for_worker(task_id)
        if not task or task.status != TaskStatus.RUNNING or task.fencing_token != fencing_token:
            return False
        attempt = await self.session.scalar(
            select(TaskAttempt.id).where(
                TaskAttempt.task_id == task.id,
                TaskAttempt.worker_id == worker_id,
                TaskAttempt.fencing_token == fencing_token,
                TaskAttempt.status == "running",
                TaskAttempt.lease_expires_at > now,
            )
        )
        return attempt is not None

    async def release_task_claim(
        self,
        task_id: str,
        worker_id: str,
        fencing_token: int,
        reason: str,
    ) -> bool:
        now = utc_now()
        task = await self._get_task_for_worker(task_id)
        if not task or task.fencing_token != fencing_token:
            return False
        async with get_session_lock(self.session):
            result = await self.session.execute(
                update(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.fencing_token == fencing_token,
                    TaskAttempt.status == "leased",
                )
                .values(
                    status="waiting",
                    finished_at=now,
                    lease_expires_at=now + timedelta(seconds=60),
                    error_message=reason[:4000],
                )
            )
            if result.rowcount == 1 and task.assigned_to:
                other_active_attempt = await self.session.scalar(
                    select(TaskAttempt.id)
                    .join(Task, Task.id == TaskAttempt.task_id)
                    .where(
                        Task.assigned_to == task.assigned_to,
                        Task.id != task.id,
                        TaskAttempt.status.in_(["leased", "running"]),
                        TaskAttempt.lease_expires_at > now,
                    )
                    .limit(1)
                )
                if not other_active_attempt:
                    await self.session.execute(
                        update(Employee)
                        .where(
                            Employee.id == task.assigned_to,
                            Employee.status == EmployeeStatus.WORKING,
                        )
                        .values(status=EmployeeStatus.IDLE)
                    )
            await self.session.commit()
        return result.rowcount == 1

    async def heartbeat_task_attempt(self, task_id: str, worker_id: str, fencing_token: int, lease_seconds: int = 60) -> Optional[str]:
        now = utc_now()
        task = await self._get_task_for_worker(task_id)
        if not task or task.fencing_token != fencing_token or task.status != TaskStatus.RUNNING:
            return None
        new_expiry = now + timedelta(seconds=lease_seconds)
        async with get_session_lock(self.session):
            result = await self.session.execute(
                update(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.fencing_token == fencing_token,
                    TaskAttempt.status == "running",
                    TaskAttempt.lease_expires_at > now,
                )
                .values(heartbeat_at=now, lease_expires_at=new_expiry)
            )
            await self.session.commit()
        return new_expiry.isoformat() if result.rowcount == 1 else None

    async def submit_task_attempt(
        self,
        task_id: str,
        worker_id: str,
        fencing_token: int,
        execution_receipt: dict[str, Any],
    ) -> bool:
        now = utc_now()
        task = await self._get_task_for_worker(task_id)
        if not task or task.fencing_token != fencing_token or task.status != TaskStatus.RUNNING:
            return False
        async with get_session_lock(self.session):
            result = await self.session.execute(
                update(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.fencing_token == fencing_token,
                    TaskAttempt.status == "running",
                    TaskAttempt.lease_expires_at > now,
                )
                .values(
                    status="submitted",
                    execution_receipt=execution_receipt,
                    finished_at=now,
                    lease_expires_at=now,
                )
            )
            await self.session.commit()
        return result.rowcount == 1

    async def _select_retry_assignee(self, task: Task) -> Optional[Employee]:
        """Choose another idle employee only when their desktop is already live."""
        if not task.assigned_to:
            return None
        now = utc_now()
        active_attempt = (
            select(TaskAttempt.id)
            .join(Task, Task.id == TaskAttempt.task_id)
            .where(
                Task.assigned_to == Employee.id,
                TaskAttempt.status.in_(["leased", "running"]),
                TaskAttempt.lease_expires_at > now,
            )
            .exists()
        )
        employees = (
            await self.session.execute(
                select(Employee)
                .join(Container, Employee.container_id == Container.id)
                .options(selectinload(Employee.skills), selectinload(Employee.container))
                .where(
                    Employee.id != task.assigned_to,
                    Employee.status == EmployeeStatus.IDLE,
                    Container.status == ContainerStatus.RUNNING,
                    ~active_attempt,
                )
                .order_by(Employee.hired_at.asc())
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
        from neural.boss.delegator import TaskDelegator

        return TaskDelegator().select_best_assignee(task, employees)

    async def fail_task_attempt(self, task_id: str, worker_id: str, fencing_token: int, error_message: str) -> Optional[dict[str, Any]]:
        now = utc_now()
        task = await self._get_task_for_worker(task_id)
        if not task or task.fencing_token != fencing_token or task.status != TaskStatus.RUNNING:
            return None
        from neural.boss.recovery import classify_failure

        classification = classify_failure(error_message)
        should_retry = classification.is_retryable and task.retry_count < task.max_retries
        retry_count = task.retry_count + (1 if should_retry else 0)
        retry_delay_seconds = min(2 ** task.retry_count, 60) if should_retry else 0
        async with get_session_lock(self.session):
            previous_assignee_id = task.assigned_to
            previous_assignee = (
                await self.session.get(Employee, previous_assignee_id)
                if previous_assignee_id
                else None
            )
            alternate_assignee = await self._select_retry_assignee(task) if should_retry else None
            if alternate_assignee:
                reserve_employee = await self.session.execute(
                    update(Employee)
                    .where(
                        Employee.id == alternate_assignee.id,
                        Employee.status == EmployeeStatus.IDLE,
                    )
                    .values(status=EmployeeStatus.WORKING)
                )
                if reserve_employee.rowcount != 1:
                    alternate_assignee = None
            attempt_update = await self.session.execute(
                update(TaskAttempt)
                .where(
                    TaskAttempt.task_id == task.id,
                    TaskAttempt.worker_id == worker_id,
                    TaskAttempt.fencing_token == fencing_token,
                    TaskAttempt.status == "running",
                    TaskAttempt.lease_expires_at > now,
                )
                .values(
                    status="failed",
                    error_message=error_message[:4000],
                    finished_at=now,
                    lease_expires_at=now + timedelta(seconds=retry_delay_seconds),
                )
            )
            if attempt_update.rowcount != 1:
                await self.session.rollback()
                return None
            task_update = await self.session.execute(
                update(Task)
                .where(Task.id == task.id, Task.fencing_token == fencing_token)
                .where(Task.status == TaskStatus.RUNNING)
                .values(
                    status=TaskStatus.ASSIGNED if should_retry else TaskStatus.FAILED,
                    assigned_to=(alternate_assignee.id if alternate_assignee else task.assigned_to),
                    retry_count=Task.retry_count + (1 if should_retry else 0),
                    completed_at=None if should_retry else now,
                    fencing_token=Task.fencing_token + 1,
                )
            )
            if task_update.rowcount != 1:
                await self.session.rollback()
                return None
            if (not should_retry or alternate_assignee) and previous_assignee_id:
                other_active_attempt = (
                    select(TaskAttempt.id)
                    .join(Task, Task.id == TaskAttempt.task_id)
                    .where(
                        Task.assigned_to == previous_assignee_id,
                        Task.id != task.id,
                        TaskAttempt.status.in_(["leased", "running"]),
                        TaskAttempt.lease_expires_at > now,
                    )
                    .exists()
                )
                await self.session.execute(
                    update(Employee)
                    .where(
                        Employee.id == previous_assignee_id,
                        Employee.status == EmployeeStatus.WORKING,
                        ~other_active_attempt,
                    )
                    .values(status=EmployeeStatus.IDLE)
                )
            await self.session.commit()
        return {
            "retrying": should_retry,
            "retry_count": retry_count,
            "retry_after_seconds": retry_delay_seconds,
            "reassigned": alternate_assignee is not None,
            "previous_employee_id": previous_assignee.employee_id if previous_assignee else None,
            "assigned_employee_id": (
                alternate_assignee.employee_id
                if alternate_assignee
                else previous_assignee.employee_id if previous_assignee else None
            ),
        }

    async def list_pending_verification(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self.session.execute(
            select(TaskAttempt, Task)
            .join(Task, Task.id == TaskAttempt.task_id)
            .where(
                TaskAttempt.status == "submitted",
                Task.status == TaskStatus.RUNNING,
                TaskAttempt.fencing_token == Task.fencing_token,
            )
            .order_by(TaskAttempt.finished_at.asc())
            .limit(max(1, min(limit, 200)))
        )
        return [
            {
                "attempt_id": attempt.id,
                "task_id": task.task_id,
                "project_id": task.project_id,
                "employee_id": task.assigned_to,
                "execution_mode": task.execution_mode.value,
                "title": task.title,
                "objective": task.objective,
                "acceptance_criteria": task.acceptance_criteria,
                "attempt_number": attempt.attempt_number,
                "worker_id": attempt.worker_id,
                "fencing_token": attempt.fencing_token,
                "submitted_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
                "execution_receipt": attempt.execution_receipt,
                "workspace_manifest_before": attempt.workspace_manifest_before,
            }
            for attempt, task in rows.all()
        ]

    async def verification_attempt_is_current(
        self,
        task_id: str,
        attempt_id: str,
        fencing_token: int,
    ) -> bool:
        result = await self.session.execute(
            select(TaskAttempt.id)
            .join(Task, Task.id == TaskAttempt.task_id)
            .where(
                Task.task_id == task_id,
                TaskAttempt.id == attempt_id,
                TaskAttempt.status == "submitted",
                Task.status == TaskStatus.RUNNING,
                TaskAttempt.fencing_token == fencing_token,
                Task.fencing_token == fencing_token,
            )
        )
        return result.scalar_one_or_none() is not None

    async def review_task_attempt(
        self,
        task_id: str,
        attempt_id: str,
        verifier_id: str,
        verification_receipt: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        now = utc_now()
        async with get_session_lock(self.session):
            task = await self.session.scalar(
                select(Task)
                .where((Task.task_id == task_id) | (Task.id == task_id))
                .with_for_update()
            )
            if not task:
                await self.session.rollback()
                return None
            attempt = await self.session.scalar(
                select(TaskAttempt)
                .where(TaskAttempt.id == attempt_id, TaskAttempt.task_id == task.id)
                .with_for_update()
            )
            if (
                not attempt
                or attempt.status != "submitted"
                or task.status != TaskStatus.RUNNING
                or task.fencing_token != attempt.fencing_token
            ):
                await self.session.rollback()
                return None

            # Bind provenance to the authenticated verifier and the active DB attempt.
            # These values must never be accepted from the verifier's request body.
            verification_receipt = {
                **verification_receipt,
                "verifier_id": verifier_id,
                "attempt_id": attempt.id,
                "task_id": task.task_id,
            }
            passed = verification_receipt["passed"]
            attempt.status = "verified" if passed else "rejected"
            attempt.verification_receipt = verification_receipt
            attempt.verified_by = verifier_id
            attempt.verified_at = now
            employee = await self.session.get(Employee, task.assigned_to) if task.assigned_to else None
            execution_receipt = attempt.execution_receipt if isinstance(attempt.execution_receipt, dict) else {}
            model_receipt = execution_receipt.get("model", {})
            if not isinstance(model_receipt, dict):
                model_receipt = {}
            try:
                cost_tokens = max(
                    0,
                    int(model_receipt.get("input_tokens", 0))
                    + int(model_receipt.get("output_tokens", 0)),
                )
                cost_usd = max(0.0, float(model_receipt.get("cost_usd", 0.0)))
            except (TypeError, ValueError):
                cost_tokens = 0
                cost_usd = 0.0
            duration_seconds = (
                max(0.0, (now - attempt.started_at).total_seconds())
                if attempt.started_at
                else 0.0
            )
            if employee:
                self.session.add(
                    PerformanceRecord(
                        id=str(uuid.uuid4()),
                        employee_id=employee.id,
                        task_id=task.task_id,
                        completion_time_seconds=duration_seconds,
                        retry_count=task.retry_count,
                        quality_score=1.0 if passed else 0.0,
                        cost_tokens=cost_tokens,
                        cost_usd=cost_usd,
                        outcome="success" if passed else "failure",
                        recorded_at=now,
                    )
                )
                summary = dict(employee.performance_summary or {})
                summary["verification_attempts"] = int(summary.get("verification_attempts", 0)) + 1
                counter = "verified_tasks" if passed else "verification_rejections"
                summary[counter] = int(summary.get(counter, 0)) + 1
                summary["last_verified_task_id"] = task.task_id
                summary["last_verifier_id"] = verifier_id
                summary["last_verification_passed"] = passed
                employee.performance_summary = summary
            if passed:
                task.status = TaskStatus.COMPLETED
                task.completed_at = now
                task.verification_result = verification_receipt
                task.fencing_token += 1
                self.session.add(
                    TaskResult(
                        id=str(uuid.uuid4()),
                        task_id=task.id,
                        summary=verification_receipt["notes"],
                        output_data=verification_receipt["test_summary"],
                        artifacts=verification_receipt["evidence_refs"],
                        verification_status=VerificationStatus.PASSED,
                        verification_notes=str(verification_receipt["checks"]),
                        submitted_at=now,
                    )
                )
                employee_id = employee.employee_id if employee else task.assigned_to
                if employee_id and valid_independent_verification_receipt(
                    verification_receipt,
                    employee_id=employee_id,
                    task_id=task.task_id,
                ):
                    # Persist the verified episodic memory in the same transaction as
                    # task completion. A stable record ID makes recovery/retries safe.
                    record_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"ancos:verified-task:{task.id}:{attempt.id}",
                        )
                    )
                    record_provenance = {
                        "employee_id": employee_id,
                        "outcome": "success",
                        "verified_by": verifier_id,
                        "independent_verification_receipt": verification_receipt,
                    }
                    self.session.add(
                        MemoryEntry(
                            id=record_id,
                            category=MemoryCategory.EPISODE,
                            source=f"task-attempt:{attempt.id}",
                            content=str(verification_receipt["notes"]),
                            confidence=1.0,
                            metadata_json={
                                "record_schema_version": 1,
                                "record_id": record_id,
                                "layer": "episodic",
                                "category": "task_receipt",
                                "access_level": "internal",
                                "verification_status": "verified",
                                "owner_employee_id": employee_id,
                                "allowed_departments": [],
                                "allowed_projects": [],
                                "version": 1,
                                "provenance": record_provenance,
                                "record_metadata": {
                                    "task_id": task.task_id,
                                    "attempt_id": attempt.id,
                                    "outcome": "success",
                                    "test_summary": verification_receipt.get("test_summary", {}),
                                },
                                "created_at": now.isoformat(),
                                "verified_at": now.isoformat(),
                                "related_record_ids": [],
                                "embedding": None,
                                "task_id": task.task_id,
                            },
                        )
                    )
                retrying = False
                retry_after_seconds = 0
            else:
                retrying = task.retry_count < task.max_retries
                retry_after_seconds = min(2 ** task.retry_count, 60) if retrying else 0
                attempt.error_message = str(verification_receipt.get("notes", "Independent verification failed."))[:4000]
                attempt.lease_expires_at = now + timedelta(seconds=retry_after_seconds)
                task.retry_count += 1 if retrying else 0
                task.status = TaskStatus.ASSIGNED if retrying else TaskStatus.FAILED
                task.completed_at = None if retrying else now
                task.fencing_token += 1
            if (passed or not retrying) and task.assigned_to:
                await self.session.execute(
                    update(Employee)
                    .where(Employee.id == task.assigned_to)
                    .values(status=EmployeeStatus.IDLE)
                )
            await self.session.commit()
            return {
                "task_id": task.task_id,
                "attempt_id": attempt.id,
                "task_status": task.status.value,
                "attempt_status": attempt.status,
                "fencing_token": task.fencing_token,
                "retrying": retrying,
                "retry_count": task.retry_count,
                "retry_after_seconds": retry_after_seconds,
            }

    async def get_by_task_id(self, task_id: str) -> Optional[Task]:
        stmt = (
            select(Task)
            .where((Task.task_id == task_id) | (Task.id == task_id))
            .options(
                selectinload(Task.dependencies),
                selectinload(Task.results),
                selectinload(Task.subtasks)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_project(self, project_id: str) -> Sequence[Task]:
        stmt = (
            select(Task)
            .where(Task.project_id == project_id)
            .order_by(Task.started_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_attempts_by_project(self, project_id: str) -> Sequence[TaskAttempt]:
        stmt = (
            select(TaskAttempt)
            .join(Task, Task.id == TaskAttempt.task_id)
            .where(Task.project_id == project_id)
            .order_by(Task.task_id.asc(), TaskAttempt.attempt_number.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_attempts_for_task(self, task_id: str) -> Sequence[TaskAttempt]:
        task = await self.get_by_task_id(task_id)
        if not task:
            return []
        result = await self.session.execute(
            select(TaskAttempt)
            .where(TaskAttempt.task_id == task.id)
            .order_by(TaskAttempt.attempt_number.asc())
        )
        return result.scalars().all()

    async def recover_unattempted_running_task(
        self,
        task_id: str,
        expected_fencing_token: int,
        expected_employee_id: str,
        approval_id: str,
        actor: str,
    ) -> Optional[dict[str, Any]]:
        """Requeue only an approved RUNNING row with no attempt or workstation lease."""
        now = utc_now()
        async with get_session_lock(self.session):
            task = await self.session.scalar(
                select(Task)
                .where(Task.task_id == task_id)
                .with_for_update()
            )
            if (
                not task
                or task.status != TaskStatus.RUNNING
                or task.fencing_token != expected_fencing_token
                or not task.assigned_to
            ):
                await self.session.rollback()
                return None

            employee = await self.session.scalar(
                select(Employee)
                .where(Employee.id == task.assigned_to)
                .with_for_update()
            )
            if not employee or employee.employee_id != expected_employee_id:
                await self.session.rollback()
                return None

            attempt_id = await self.session.scalar(
                select(TaskAttempt.id).where(TaskAttempt.task_id == task.id).limit(1)
            )
            if attempt_id:
                await self.session.rollback()
                return None

            docker_id = f"ancos-desktop-{employee.employee_id.lower()}"
            blocking_lease = await self.session.scalar(
                select(ResourceLease.id)
                .where(
                    or_(
                        ResourceLease.employee_id.in_([employee.id, employee.employee_id]),
                        ResourceLease.resource_key == docker_id,
                    ),
                    ResourceLease.status.in_(("active", "expired")),
                )
                .limit(1)
            )
            if blocking_lease:
                await self.session.rollback()
                return None

            approval = await self.session.scalar(
                select(Approval)
                .where(Approval.id == approval_id)
                .with_for_update()
            )
            expected_details = {
                "task_id": task.task_id,
                "project_id": task.project_id,
                "expected_fencing_token": expected_fencing_token,
                "employee_id": employee.employee_id,
            }
            if (
                not approval
                or approval.action_type != "RECOVER_ORPHANED_TASK"
                or approval.status != ApprovalStatus.APPROVED
                or approval.consumed_at is not None
                or approval.requested_by != actor
                or not approval.decided_by
                or approval.decided_by == approval.requested_by
                or any(approval.details.get(key) != value for key, value in expected_details.items())
            ):
                await self.session.rollback()
                return None

            # The API has independently confirmed the broker returns 404 for the
            # deterministic employee container. If the DB still claims RUNNING,
            # correct that stale cache in the same recovery transaction.
            container = (
                await self.session.get(Container, employee.container_id)
                if employee.container_id
                else None
            )
            if container and container.status == ContainerStatus.RUNNING:
                container.status = ContainerStatus.FAILED

            previous_fencing_token = task.fencing_token
            task.status = TaskStatus.PENDING
            task.assigned_to = None
            task.fencing_token += 1

            other_active_task = await self.session.scalar(
                select(Task.id)
                .where(
                    Task.assigned_to == employee.id,
                    Task.id != task.id,
                    Task.status.in_((TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.RETRYING)),
                )
                .limit(1)
            )
            other_active_attempt = await self.session.scalar(
                select(TaskAttempt.id)
                .join(Task, Task.id == TaskAttempt.task_id)
                .where(
                    Task.assigned_to == employee.id,
                    TaskAttempt.status.in_(("leased", "running")),
                    TaskAttempt.lease_expires_at > now,
                )
                .limit(1)
            )
            if not other_active_task and not other_active_attempt:
                employee.status = EmployeeStatus.IDLE

            approval.consumed_at = now
            approval.consumed_by = actor
            audit_entry = await AuditRepository(self.session).create_entry(
                actor=actor,
                action="TASK_RECOVERY_REQUEUED",
                resource_type="Task",
                resource_id=task.task_id,
                details={
                    "approval_id": approval.id,
                    "approver": approval.decided_by,
                    "previous_status": TaskStatus.RUNNING.value,
                    "new_status": TaskStatus.PENDING.value,
                    "previous_employee_id": employee.employee_id,
                    "fencing_token_before": previous_fencing_token,
                    "fencing_token_after": task.fencing_token,
                    "reason": approval.justification,
                },
            )
            await self.session.commit()
            await self.session.refresh(task)
            await self.session.refresh(audit_entry)
            return {
                "task_id": task.task_id,
                "status": task.status.value,
                "assigned_to": task.assigned_to,
                "fencing_token": task.fencing_token,
                "employee_id": employee.employee_id,
                "employee_status": employee.status.value,
                "approval_id": approval.id,
                "audit_id": audit_entry.id,
            }

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        enforce_verification: Optional[bool] = None
    ) -> None:
        task = await self.get_by_task_id(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if status == TaskStatus.COMPLETED:
            v_res = task.verification_result
            if not v_res or not v_res.get("passed"):
                raise UnverifiedTaskError(
                    f"Strict gate violation: Task {task_id} cannot transition to completed without an independent verification receipt."
                )

        values: dict[str, Any] = {"status": status}
        if status != TaskStatus.RUNNING:
            values["fencing_token"] = Task.fencing_token + 1
        if status == TaskStatus.RUNNING:
            values["started_at"] = utc_now()
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            values["completed_at"] = utc_now()
        elif status == TaskStatus.RETRYING:
            values["retry_count"] = Task.retry_count + 1
        stmt = (
            update(Task)
            .where((Task.task_id == task_id) | (Task.id == task_id))
            .values(**values)
        )
        lock = get_session_lock(self.session)
        async with lock:
            if status == TaskStatus.CANCELLED:
                now = utc_now()
                await self.session.execute(
                    update(TaskAttempt)
                    .where(
                        TaskAttempt.task_id == task.id,
                        TaskAttempt.fencing_token == task.fencing_token,
                        TaskAttempt.status.in_(["leased", "running"]),
                    )
                    .values(
                        status="cancelled",
                        finished_at=now,
                        lease_expires_at=now,
                        error_message="Task was cancelled by an authorized control-plane action.",
                    )
                )
            await self.session.execute(stmt)
            await self.session.commit()

    async def recover_running_after_emergency_stop(self) -> int:
        """Requeue running tasks and invalidate their active worker attempts atomically."""
        now = utc_now()
        async with get_session_lock(self.session):
            running = (
                await self.session.execute(
                    select(Task.id, Task.fencing_token)
                    .where(Task.status == TaskStatus.RUNNING)
                    .with_for_update()
                )
            ).all()

            recovered = 0
            for task_id, fencing_token in running:
                await self.session.execute(
                    update(TaskAttempt)
                    .where(
                        TaskAttempt.task_id == task_id,
                        TaskAttempt.fencing_token == fencing_token,
                        TaskAttempt.status.in_(["leased", "running"]),
                    )
                    .values(
                        status="cancelled",
                        finished_at=now,
                        lease_expires_at=now,
                        error_message=(
                            "Emergency-stop recovery cancelled this attempt and superseded its lease."
                        ),
                    )
                )
                result = await self.session.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,
                        Task.status == TaskStatus.RUNNING,
                        Task.fencing_token == fencing_token,
                    )
                    .values(
                        status=TaskStatus.PENDING,
                        fencing_token=fencing_token + 1,
                        started_at=None,
                    )
                )
                recovered += result.rowcount or 0

            await self.session.commit()
            return recovered

    async def complete_with_receipt(
        self,
        task_id: str,
        verification_receipt: dict[str, Any]
    ) -> Task:
        """
        Completes a task with an independent passing verification receipt.
        Enforces strict gate: raises VerificationFailedError if receipt failed or missing.
        """
        if not verification_receipt or not verification_receipt.get("passed"):
            raise VerificationFailedError(
                f"Task {task_id} cannot be marked completed: verification receipt is invalid or failed."
            )
        lock = get_session_lock(self.session)
        async with lock:
            stmt = (
                update(Task)
                .where((Task.task_id == task_id) | (Task.id == task_id))
                .values(
                    status=TaskStatus.COMPLETED,
                    completed_at=utc_now(),
                    verification_result=verification_receipt,
                    fencing_token=Task.fencing_token + 1,
                )
            )
            await self.session.execute(stmt)

            task = await self.get_by_task_id(task_id)
            if not task:
                raise ValueError(f"Task {task_id} not found")

            # Persist TaskResult record
            task_result = TaskResult(
                id=str(uuid.uuid4()),
                task_id=task.id,
                summary=verification_receipt.get("notes", "Task independently verified with passing receipt."),
                output_data=verification_receipt.get("test_summary", {}),
                artifacts=verification_receipt.get("artifacts", []),
                verification_status=VerificationStatus.PASSED,
                verification_notes=str(verification_receipt.get("details", "")),
                submitted_at=utc_now()
            )
            self.session.add(task_result)
            await self.session.commit()
            await self.session.refresh(task)
            return task

    async def assign_task(self, task_id: str, employee_id: str) -> None:
        stmt_emp = select(Employee).where(
            (Employee.employee_id == employee_id) | (Employee.id == employee_id)
        )
        lock = get_session_lock(self.session)
        async with lock:
            res = await self.session.execute(stmt_emp)
            emp = res.scalar_one_or_none()
            emp_id = emp.id if emp else employee_id

            stmt = (
                update(Task)
                .where((Task.task_id == task_id) | (Task.id == task_id))
                .values(
                    assigned_to=emp_id,
                    status=TaskStatus.ASSIGNED,
                    fencing_token=Task.fencing_token + 1,
                )
            )
            await self.session.execute(stmt)
            await self.session.commit()

    async def list_by_assignee(self, employee_id: str) -> Sequence[Task]:
        stmt = (
            select(Task)
            .join(Employee, Task.assigned_to == Employee.id, isouter=True)
            .where((Task.assigned_to == employee_id) | (Employee.employee_id == employee_id))
            .order_by(Task.started_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_all(self) -> Sequence[Task]:
        stmt = select(Task).order_by(Task.started_at.asc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_by_status(self, status: TaskStatus) -> Sequence[Task]:
        stmt = (
            select(Task)
            .where(Task.status == status)
            .order_by(Task.started_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
    ) -> Sequence[Task]:
        stmt = select(Task)
        if status:
            stmt = stmt.where(Task.status == status)
        stmt = stmt.order_by(Task.started_at.asc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()



    async def create_subtask(
        self,
        parent_task_id: str,
        title: str,
        objective: Optional[str] = None,
        scope: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        required_capabilities: Optional[list[str]] = None,
        priority: Optional[TaskPriority] = None,
        task_id: Optional[str] = None
    ) -> Task:
        parent = await self.get_by_task_id(parent_task_id)
        if not parent:
            raise ValueError(f"Parent task {parent_task_id} not found")

        tid = task_id or f"{parent.task_id}-SUB-{uuid.uuid4().hex[:4].upper()}"
        subtask = Task(
            task_id=tid,
            project_id=parent.project_id,
            parent_task_id=parent.id,
            title=title,
            objective=objective or title,
            scope=scope or parent.scope,
            acceptance_criteria=acceptance_criteria or parent.acceptance_criteria,
            required_capabilities=required_capabilities or parent.required_capabilities,
            priority=priority or parent.priority,
            status=TaskStatus.PENDING,
            resource_budget=parent.resource_budget
        )
        self.session.add(subtask)
        await self.session.commit()
        await self.session.refresh(subtask)
        return subtask

    async def list_subtasks(self, parent_task_id: str) -> Sequence[Task]:
        parent = await self.get_by_task_id(parent_task_id)
        p_id = parent.id if parent else parent_task_id
        stmt = select(Task).where(Task.parent_task_id == p_id).order_by(Task.started_at.asc())
        result = await self.session.execute(stmt)
        return result.scalars().all()


    async def add_dependency(
        self,
        task_id: str,
        depends_on_task_id: str,
        dependency_type: str = "blocks"
    ) -> TaskDependency:
        # Resolve task_id and depends_on_task_id to primary key IDs if public task_ids were passed
        task = await self.get_by_task_id(task_id)
        dep_task = await self.get_by_task_id(depends_on_task_id)

        t_id = task.id if task else task_id
        dep_id = dep_task.id if dep_task else depends_on_task_id

        dep = TaskDependency(
            task_id=t_id,
            depends_on_task_id=dep_id,
            dependency_type=dependency_type
        )
        self.session.add(dep)
        await self.session.commit()
        await self.session.refresh(dep)
        return dep

    async def list_dependencies_for_project(self, project_id: str) -> list[tuple[str, str]]:
        """
        Returns list of (task_id, depends_on_task_id) public task_id strings for a project.
        """
        stmt = (
            select(TaskDependency, Task)
            .join(Task, TaskDependency.task_id == Task.id)
            .where(Task.project_id == project_id)
        )
        res = await self.session.execute(stmt)
        deps = res.all()
        # Resolve UUIDs back to public task_ids
        task_lookup_stmt = select(Task.id, Task.task_id).where(Task.project_id == project_id)
        lookup_res = await self.session.execute(task_lookup_stmt)
        uuid_to_task_id = dict(lookup_res.all())

        results: list[tuple[str, str]] = []
        for dep_row in deps:
            dep_obj = dep_row[0]
            tid = uuid_to_task_id.get(dep_obj.task_id, dep_obj.task_id)
            dep_tid = uuid_to_task_id.get(dep_obj.depends_on_task_id, dep_obj.depends_on_task_id)
            results.append((tid, dep_tid))
        return results

    async def list_dependencies_for_task(self, task_id: str) -> Sequence[TaskDependency]:
        task = await self.get_by_task_id(task_id)
        t_id = task.id if task else task_id
        stmt = select(TaskDependency).where(TaskDependency.task_id == t_id)
        res = await self.session.execute(stmt)
        return res.scalars().all()



class ResourceLeaseRepository:
    """Transactional, restart-safe leases for desktop slots and other scarce resources."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _as_dict(lease: ResourceLease) -> dict[str, Any]:
        return {
            "lease_id": lease.id,
            "resource_key": lease.resource_key,
            "employee_id": lease.employee_id,
            "task_id": lease.task_id,
            "session_id": lease.session_id,
            "fencing_token": lease.fencing_token,
            "status": lease.status,
            "granted_at": lease.granted_at.isoformat(),
            "heartbeat_at": lease.heartbeat_at.isoformat(),
            "lease_expires_at": lease.lease_expires_at.isoformat(),
            "resource_reservation": lease.resource_reservation,
        }

    async def claim(
        self,
        resource_key: str,
        employee_id: str,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        lease_seconds: int = 60,
        resource_reservation: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        now = utc_now()
        ttl = max(5, min(lease_seconds, 3600))
        async with get_session_lock(self.session):
            leases = (
                await self.session.execute(
                    select(ResourceLease)
                    .where(
                        or_(
                            ResourceLease.resource_key == resource_key,
                            ResourceLease.employee_id == employee_id,
                        ),
                        ResourceLease.status.in_(("active", "expired")),
                    )
                    .with_for_update()
                )
            ).scalars().all()
            if leases:
                newly_expired = False
                for lease in leases:
                    if lease.status == "active" and lease.lease_expires_at <= now:
                        # Expiry fences the old owner but does not prove the desktop
                        # process stopped. Keep the resource quarantined until the
                        # caller confirms cleanup and explicitly releases this fence.
                        lease.status = "expired"
                        newly_expired = True
                if newly_expired:
                    await self.session.commit()
                else:
                    await self.session.rollback()
                return None

            previous_fence = await self.session.scalar(
                select(func.max(ResourceLease.fencing_token)).where(ResourceLease.resource_key == resource_key)
            )
            lease = ResourceLease(
                resource_key=resource_key,
                employee_id=employee_id,
                task_id=task_id,
                session_id=session_id,
                fencing_token=(previous_fence or 0) + 1,
                status="active",
                granted_at=now,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=ttl),
                resource_reservation=resource_reservation or {},
            )
            self.session.add(lease)
            try:
                await self.session.commit()
            except IntegrityError:
                await self.session.rollback()
                return None
            await self.session.refresh(lease)
            return self._as_dict(lease)

    async def get_by_session_id(self, session_id: str) -> Optional[dict[str, Any]]:
        result = await self.session.execute(
            select(ResourceLease)
            .where(ResourceLease.session_id == session_id)
            .order_by(
                case(
                    (ResourceLease.status == "active", 0),
                    (ResourceLease.status == "expired", 1),
                    else_=2,
                ),
                ResourceLease.granted_at.desc(),
                ResourceLease.fencing_token.desc(),
            )
            .limit(1)
        )
        lease = result.scalar_one_or_none()
        return self._as_dict(lease) if lease else None

    async def list_blocking_for_employee(
        self,
        employee_id: str,
        internal_employee_id: Optional[str] = None,
        resource_key: Optional[str] = None,
    ) -> Sequence[dict[str, Any]]:
        identities = [employee_id]
        if internal_employee_id and internal_employee_id not in identities:
            identities.append(internal_employee_id)
        filters = [ResourceLease.employee_id.in_(identities)]
        if resource_key:
            filters.append(ResourceLease.resource_key == resource_key)
        result = await self.session.execute(
            select(ResourceLease)
            .where(
                or_(*filters),
                ResourceLease.status.in_(("active", "expired")),
            )
            .order_by(ResourceLease.granted_at.asc())
        )
        return [self._as_dict(lease) for lease in result.scalars().all()]

    async def heartbeat(self, lease_id: str, fencing_token: int, lease_seconds: int = 60) -> Optional[str]:
        now = utc_now()
        expires = now + timedelta(seconds=max(5, min(lease_seconds, 3600)))
        async with get_session_lock(self.session):
            result = await self.session.execute(
                update(ResourceLease)
                .where(
                    ResourceLease.id == lease_id,
                    ResourceLease.fencing_token == fencing_token,
                    ResourceLease.status == "active",
                    ResourceLease.lease_expires_at > now,
                )
                .values(heartbeat_at=now, lease_expires_at=expires)
            )
            await self.session.commit()
        return expires.isoformat() if result.rowcount == 1 else None

    async def release(self, lease_id: str, fencing_token: int) -> bool:
        now = utc_now()
        async with get_session_lock(self.session):
            result = await self.session.execute(
                update(ResourceLease)
                .where(
                    ResourceLease.id == lease_id,
                    ResourceLease.fencing_token == fencing_token,
                    ResourceLease.status.in_(("active", "expired")),
                )
                .values(status="released", released_at=now)
            )
            await self.session.commit()
        return result.rowcount == 1

    async def reconcile_expired(self) -> int:
        """Fence expired owners without freeing resources for reuse."""
        now = utc_now()
        async with get_session_lock(self.session):
            result = await self.session.execute(
                update(ResourceLease)
                .where(ResourceLease.status == "active", ResourceLease.lease_expires_at <= now)
                .values(status="expired")
            )
            await self.session.commit()
        return result.rowcount or 0

    async def list_expired(self) -> Sequence[dict[str, Any]]:
        """Returns quarantined leases for a cleanup worker to inspect."""
        await self.reconcile_expired()
        result = await self.session.execute(
            select(ResourceLease)
            .where(ResourceLease.status == "expired")
            .order_by(ResourceLease.granted_at.asc())
        )
        return [self._as_dict(lease) for lease in result.scalars().all()]


class EmergencyStopRepository:
    """Read/write the single durable ANCOS emergency-stop state."""

    STATE_KEY = "global"

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_state(self) -> dict[str, Any]:
        state = await self.session.get(EmergencyStopState, self.STATE_KEY)
        if state is None:
            return {
                "active": False,
                "reason": None,
                "changed_by": None,
                "changed_at": None,
            }
        return {
            "active": state.active,
            "reason": state.reason,
            "changed_by": state.changed_by,
            "changed_at": state.changed_at,
        }

    async def set_state(self, *, active: bool, actor: str, reason: str | None) -> dict[str, Any]:
        now = utc_now()
        values = {
            "active": active,
            "reason": reason,
            "changed_by": actor,
            "changed_at": now,
        }
        result = await self.session.execute(
            update(EmergencyStopState)
            .where(EmergencyStopState.state_key == self.STATE_KEY)
            .values(**values)
        )
        if result.rowcount == 0:
            self.session.add(EmergencyStopState(state_key=self.STATE_KEY, **values))
        try:
            await self.session.commit()
        except IntegrityError:
            # Concurrent first writers can race to create the singleton row.
            # Roll back and apply the requested state to the now-existing row.
            await self.session.rollback()
            await self.session.execute(
                update(EmergencyStopState)
                .where(EmergencyStopState.state_key == self.STATE_KEY)
                .values(**values)
            )
            await self.session.commit()
        return {**values}


class ServiceHeartbeatRepository:
    """Persists the latest authenticated liveness receipt per service identity."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        service_type: str,
        service_id: str,
        instance_id: str,
    ) -> ServiceHeartbeat:
        if service_type not in {"worker", "verifier"}:
            raise ValueError("Unsupported service heartbeat type.")
        now = utc_now()
        async with get_session_lock(self.session):
            heartbeat = await self.session.scalar(
                select(ServiceHeartbeat).where(
                    ServiceHeartbeat.service_type == service_type,
                    ServiceHeartbeat.service_id == service_id,
                )
            )
            if heartbeat:
                if heartbeat.instance_id != instance_id:
                    heartbeat.started_at = now
                    heartbeat.instance_id = instance_id
                heartbeat.last_seen_at = now
            else:
                heartbeat = ServiceHeartbeat(
                    service_type=service_type,
                    service_id=service_id,
                    instance_id=instance_id,
                    started_at=now,
                    last_seen_at=now,
                )
                self.session.add(heartbeat)
            try:
                await self.session.commit()
            except IntegrityError:
                # Two replicas can register the same identity at startup. The
                # unique key selects one row; the loser refreshes that row.
                await self.session.rollback()
                heartbeat = await self.session.scalar(
                    select(ServiceHeartbeat).where(
                        ServiceHeartbeat.service_type == service_type,
                        ServiceHeartbeat.service_id == service_id,
                    )
                )
                if heartbeat is None:
                    raise
                if heartbeat.instance_id != instance_id:
                    heartbeat.started_at = now
                    heartbeat.instance_id = instance_id
                heartbeat.last_seen_at = now
                await self.session.commit()
            await self.session.refresh(heartbeat)
            return heartbeat

    async def list_all(self) -> Sequence[ServiceHeartbeat]:
        result = await self.session.execute(
            select(ServiceHeartbeat).order_by(
                ServiceHeartbeat.service_type.asc(),
                ServiceHeartbeat.service_id.asc(),
            )
        )
        return result.scalars().all()


class EmployeeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs: Any) -> Employee:
        employee = Employee(**kwargs)
        self.session.add(employee)
        await self.session.commit()
        await self.session.refresh(employee)
        return employee

    async def get_by_employee_id(self, employee_id: str) -> Optional[Employee]:
        stmt = (
            select(Employee)
            .where((Employee.employee_id == employee_id) | (Employee.id == employee_id))
            .options(
                selectinload(Employee.skills),
                selectinload(Employee.container)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


    async def list_all(self) -> Sequence[Employee]:
        stmt = (
            select(Employee)
            .options(selectinload(Employee.skills))
            .order_by(Employee.hired_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def add_skill(self, employee_id: str, skill_name: str, level: Any, verified: bool = False) -> EmployeeSkill:
        emp = await self.get_by_employee_id(employee_id)
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")
        skill = EmployeeSkill(
            employee_id=emp.id,
            skill_name=skill_name,
            level=level,
            verified=verified
        )
        self.session.add(skill)
        if skill not in emp.skills:
            emp.skills.append(skill)
        await self.session.commit()
        await self.session.refresh(skill)
        return skill

    async def verify_skill(self, employee_id: str, skill_name: str, task_evidence_id: str) -> None:
        emp = await self.get_by_employee_id(employee_id)
        if not emp:
            return
        stmt = (
            select(EmployeeSkill)
            .where(
                EmployeeSkill.employee_id == emp.id,
                EmployeeSkill.skill_name == skill_name
            )
        )
        result = await self.session.execute(stmt)
        skill = result.scalar_one_or_none()
        if skill:
            skill.verified = True
            skill.verified_at = utc_now()
            ev = list(skill.evidence_task_ids or [])
            if task_evidence_id not in ev:
                ev.append(task_evidence_id)
            skill.evidence_task_ids = ev
        else:
            # Dynamically certify skill if not already present on employee
            skill = EmployeeSkill(
                id=str(uuid.uuid4()),
                employee_id=emp.id,
                skill_name=skill_name,
                level=SkillLevel.INTERMEDIATE,
                verified=True,
                verified_at=utc_now(),
                evidence_task_ids=[task_evidence_id]
            )
            self.session.add(skill)
            if skill not in emp.skills:
                emp.skills.append(skill)

        # Update performance summary verified_skills
        perf = dict(emp.performance_summary or {})
        v_skills = list(perf.get("verified_skills", []))
        if skill_name not in v_skills:
            v_skills.append(skill_name)
        perf["verified_skills"] = v_skills
        emp.performance_summary = perf

        await self.session.commit()

    async def list_verified_skills(self, employee_id: str) -> list[str]:
        emp = await self.get_by_employee_id(employee_id)
        if not emp:
            return []
        return [s.skill_name for s in emp.skills if s.verified]

    async def has_verified_skill(self, employee_id: str, skill_name: str) -> bool:
        verified_skills = await self.list_verified_skills(employee_id)
        return any(s.lower() == skill_name.lower() for s in verified_skills)

    async def assign_container(self, employee_id: str, container_id: str) -> None:
        emp = await self.get_by_employee_id(employee_id)
        if emp:
            emp.container_id = container_id
            await self.session.commit()

    async def update_status(self, employee_id: str, status: EmployeeStatus) -> None:
        emp = await self.get_by_employee_id(employee_id)
        if emp:
            emp.status = status
            await self.session.commit()

    async def upgrade_skill_level(
        self,
        employee_id: str,
        skill_name: str,
        new_level: SkillLevel,
        task_evidence_id: Optional[str] = None
    ) -> EmployeeSkill:
        emp = await self.get_by_employee_id(employee_id)
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")

        stmt = select(EmployeeSkill).where(
            EmployeeSkill.employee_id == emp.id,
            EmployeeSkill.skill_name == skill_name
        )
        res = await self.session.execute(stmt)
        skill = res.scalar_one_or_none()

        if skill:
            skill.level = new_level
            skill.verified = True
            skill.verified_at = utc_now()
            if task_evidence_id:
                ev = list(skill.evidence_task_ids or [])
                if task_evidence_id not in ev:
                    ev.append(task_evidence_id)
                skill.evidence_task_ids = ev
        else:
            skill = EmployeeSkill(
                id=str(uuid.uuid4()),
                employee_id=emp.id,
                skill_name=skill_name,
                level=new_level,
                verified=True,
                verified_at=utc_now(),
                evidence_task_ids=[task_evidence_id] if task_evidence_id else []
            )
            self.session.add(skill)
            if skill not in emp.skills:
                emp.skills.append(skill)

        perf = dict(emp.performance_summary or {})
        v_skills = list(perf.get("verified_skills", []))
        if skill_name not in v_skills:
            v_skills.append(skill_name)
        perf["verified_skills"] = v_skills
        levels_map = dict(perf.get("skill_levels", {}))
        levels_map[skill_name] = new_level.value if hasattr(new_level, "value") else str(new_level)
        perf["skill_levels"] = levels_map
        emp.performance_summary = perf

        await self.session.commit()
        await self.session.refresh(skill)
        return skill

    async def get_skill_matrix(self) -> list[dict[str, Any]]:
        employees = await self.list_all()
        matrix = []
        for emp in employees:
            emp_skills = {}
            for s in emp.skills:
                emp_skills[s.skill_name] = {
                    "level": s.level.value if hasattr(s.level, "value") else str(s.level),
                    "verified": s.verified,
                    "verified_at": s.verified_at.isoformat() if s.verified_at else None,
                    "evidence_count": len(s.evidence_task_ids or []),
                }
            matrix.append({
                "employee_id": emp.employee_id,
                "name": emp.name,
                "title": emp.title,
                "department": emp.department,
                "status": emp.status.value if hasattr(emp.status, "value") else str(emp.status),
                "skills": emp_skills,
                "total_skills": len(emp.skills),
                "verified_skills_count": len([s for s in emp.skills if s.verified]),
            })
        return matrix

    async def get_employee_progression(self, employee_id: str) -> dict[str, Any]:
        emp = await self.get_by_employee_id(employee_id)
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")
        skills_info = []
        for s in emp.skills:
            skills_info.append({
                "skill_name": s.skill_name,
                "level": s.level.value if hasattr(s.level, "value") else str(s.level),
                "verified": s.verified,
                "verified_at": s.verified_at.isoformat() if s.verified_at else None,
                "evidence_task_ids": s.evidence_task_ids or [],
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            })
        return {
            "employee_id": emp.employee_id,
            "name": emp.name,
            "title": emp.title,
            "department": emp.department,
            "status": emp.status.value if hasattr(emp.status, "value") else str(emp.status),
            "hired_at": emp.hired_at.isoformat() if emp.hired_at else None,
            "skills": skills_info,
            "performance_summary": emp.performance_summary or {},
            "desk_id": emp.desk_id,
        }

    async def assign_desk(self, employee_id: str, desk_id: str) -> Optional[Employee]:
        emp = await self.get_by_employee_id(employee_id)
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")

        stmt = (
            select(Employee)
            .where(
                Employee.desk_id == desk_id,
                Employee.id != emp.id,
                Employee.employee_id != emp.employee_id,
                Employee.status != EmployeeStatus.TERMINATED,
            )
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing:
            raise ValueError(f"Workstation desk '{desk_id}' is already assigned to active employee '{existing.employee_id}'")

        emp.desk_id = desk_id
        await self.session.commit()
        await self.session.refresh(emp)
        return emp

    async def release_desk(self, employee_id: str) -> Optional[Employee]:
        emp = await self.get_by_employee_id(employee_id)
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")
        emp.desk_id = None
        await self.session.commit()
        await self.session.refresh(emp)
        return emp

    async def get_by_desk_id(self, desk_id: str) -> Optional[Employee]:
        stmt = (
            select(Employee)
            .where(Employee.desk_id == desk_id, Employee.status != EmployeeStatus.TERMINATED)
            .options(selectinload(Employee.skills))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_occupied_desks(self) -> set[str]:
        stmt = select(Employee.desk_id).where(
            Employee.desk_id.isnot(None),
            Employee.status != EmployeeStatus.TERMINATED,
        )
        result = await self.session.execute(stmt)
        return {r[0] for r in result.all() if r[0]}



class ContainerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs: Any) -> Container:
        container = Container(**kwargs)
        self.session.add(container)
        await self.session.commit()
        await self.session.refresh(container)
        return container

    async def upsert_current(self, **kwargs: Any) -> Container:
        """Persist the latest lifecycle record for a deterministic Docker ID."""
        docker_id = kwargs.get("docker_id")
        if not docker_id:
            raise ValueError("docker_id is required to upsert a workstation record")
        kwargs.setdefault("started_at", utc_now())

        container = await self.get_by_docker_id(docker_id)
        if container is None:
            container = Container(**kwargs)
            self.session.add(container)
        else:
            if kwargs["started_at"] <= container.started_at:
                kwargs["started_at"] = container.started_at + timedelta(microseconds=1)
            for key, value in kwargs.items():
                setattr(container, key, value)

        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        await self.session.refresh(container)
        return container

    async def get_by_docker_id(self, docker_id: str) -> Optional[Container]:
        stmt = select(Container).where(Container.docker_id == docker_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, container_id: str) -> Optional[Container]:
        stmt = select(Container).where(Container.id == container_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(self, docker_id: str, status: ContainerStatus) -> None:
        stmt = (
            update(Container)
            .where(Container.docker_id == docker_id)
            .values(status=status)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def list_all(self) -> Sequence[Container]:
        stmt = select(Container).order_by(Container.started_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()


class MeetingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs: Any) -> Meeting:
        meeting = Meeting(**kwargs)
        self.session.add(meeting)
        await self.session.commit()
        await self.session.refresh(meeting)
        return meeting

    async def get_by_meeting_id(self, meeting_id: str) -> Optional[Meeting]:
        stmt = (
            select(Meeting)
            .where((Meeting.meeting_id == meeting_id) | (Meeting.id == meeting_id))
            .options(selectinload(Meeting.action_items))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 20) -> Sequence[Meeting]:
        stmt = (
            select(Meeting)
            .options(selectinload(Meeting.action_items))
            .order_by(Meeting.scheduled_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_status(self, meeting_id: str, status: MeetingStatus, **kwargs: Any) -> Optional[Meeting]:
        meeting = await self.get_by_meeting_id(meeting_id)
        if not meeting:
            return None
        meeting.status = status
        for k, v in kwargs.items():
            if hasattr(meeting, k):
                setattr(meeting, k, v)
        await self.session.commit()
        await self.session.refresh(meeting)
        return meeting

    async def record_decision(self, meeting_id: str, decision: dict[str, Any]) -> Optional[Meeting]:
        meeting = await self.get_by_meeting_id(meeting_id)
        if not meeting:
            return None
        decisions = list(meeting.decisions or [])
        decisions.append(decision)
        meeting.decisions = decisions
        await self.session.commit()
        await self.session.refresh(meeting)
        return meeting

    async def update_minutes(self, meeting_id: str, minutes: dict[str, Any]) -> Optional[Meeting]:
        meeting = await self.get_by_meeting_id(meeting_id)
        if not meeting:
            return None
        meeting.minutes = minutes
        await self.session.commit()
        await self.session.refresh(meeting)
        return meeting

    async def update_speaker(self, meeting_id: str, speaker_id: Optional[str]) -> Optional[Meeting]:
        meeting = await self.get_by_meeting_id(meeting_id)
        if not meeting:
            return None
        meeting.active_speaker_id = speaker_id
        await self.session.commit()
        await self.session.refresh(meeting)
        return meeting


def normalize_action_item_desc(d: str) -> str:
    cleaned = d or ""
    while cleaned.startswith("[Carried Over]"):
        cleaned = cleaned[len("[Carried Over]"):].strip()
    return cleaned.strip()


def action_item_transition_allowed(current: ActionItemStatus, target: ActionItemStatus) -> bool:
    """Return whether a boardroom action item may move to the requested state."""
    if current == target:
        return True
    transitions = {
        ActionItemStatus.OPEN: {
            ActionItemStatus.IN_PROGRESS,
            ActionItemStatus.RESOLVED,
            ActionItemStatus.CANCELLED,
        },
        ActionItemStatus.IN_PROGRESS: {
            ActionItemStatus.RESOLVED,
            ActionItemStatus.CANCELLED,
        },
        ActionItemStatus.RESOLVED: set(),
        ActionItemStatus.CANCELLED: set(),
    }
    return target in transitions.get(current, set())


def parse_deadline_datetime(deadline: Any) -> Optional[datetime]:
    if not deadline:
        return None
    if isinstance(deadline, datetime):
        return deadline.astimezone(timezone.utc).replace(tzinfo=None) if deadline.tzinfo else deadline
    if isinstance(deadline, str):
        try:
            dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return None
    return None


class ActionItemRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        meeting_id: str,
        description: str,
        assigned_to: str,
        deadline: Optional[datetime | str] = None,
        status: ActionItemStatus = ActionItemStatus.OPEN
    ) -> ActionItem:
        # Resolve meeting_id to primary key UUID if meeting_id string like ANC-MTG-XXXX was passed
        stmt_m = select(Meeting).where((Meeting.meeting_id == meeting_id) | (Meeting.id == meeting_id))
        res_m = await self.session.execute(stmt_m)
        meeting = res_m.scalar_one_or_none()
        m_uuid = meeting.id if meeting else meeting_id

        parsed_deadline = parse_deadline_datetime(deadline)

        item = ActionItem(
            id=str(uuid.uuid4()),
            meeting_id=m_uuid,
            description=description,
            assigned_to=assigned_to,
            deadline=parsed_deadline,
            status=status,
            created_at=utc_now()
        )
        self.session.add(item)
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def get_by_id(self, action_item_id: str) -> Optional[ActionItem]:
        stmt = select(ActionItem).where(ActionItem.id == action_item_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_meeting(self, meeting_id: str) -> Sequence[ActionItem]:
        stmt_m = select(Meeting).where((Meeting.meeting_id == meeting_id) | (Meeting.id == meeting_id))
        res_m = await self.session.execute(stmt_m)
        meeting = res_m.scalar_one_or_none()
        m_uuid = meeting.id if meeting else meeting_id

        stmt = select(ActionItem).where(ActionItem.meeting_id == m_uuid).order_by(ActionItem.created_at.asc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_all(self) -> Sequence[ActionItem]:
        stmt = select(ActionItem).order_by(ActionItem.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_unresolved(self) -> Sequence[ActionItem]:
        stmt = (
            select(ActionItem)
            .where(ActionItem.status.in_([ActionItemStatus.OPEN, ActionItemStatus.IN_PROGRESS]))
            .order_by(ActionItem.created_at.desc())
        )
        result = await self.session.execute(stmt)
        all_unresolved = result.scalars().all()
        # Deduplicate so carried-over instances of the same deliverable are not counted multiple times
        seen: dict[tuple[str, str], ActionItem] = {}
        for item in all_unresolved:
            key = (normalize_action_item_desc(item.description), item.assigned_to)
            if key not in seen:
                seen[key] = item
        # Return in ascending order of creation
        return sorted(seen.values(), key=lambda x: x.created_at)

    async def list_overdue(self) -> Sequence[ActionItem]:
        now = utc_now()
        stmt = (
            select(ActionItem)
            .where(
                ActionItem.status.in_([ActionItemStatus.OPEN, ActionItemStatus.IN_PROGRESS]),
                ActionItem.deadline.isnot(None),
                ActionItem.deadline < now
            )
            .order_by(ActionItem.deadline.asc())
        )
        result = await self.session.execute(stmt)
        all_overdue = result.scalars().all()
        seen: dict[tuple[str, str], ActionItem] = {}
        for item in all_overdue:
            key = (normalize_action_item_desc(item.description), item.assigned_to)
            if key not in seen:
                seen[key] = item
        return list(seen.values())

    async def update_status(self, action_item_id: str, status: ActionItemStatus) -> Optional[ActionItem]:
        item = await self.get_by_id(action_item_id)
        if not item:
            return None
        if not action_item_transition_allowed(item.status, status):
            raise ValueError(
                f"Action item cannot transition from '{item.status.value}' to '{status.value}'."
            )
        result = await self.session.execute(
            update(ActionItem)
            .where(ActionItem.id == item.id, ActionItem.status == item.status)
            .values(status=status)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise ValueError("Action item status changed concurrently; reload it and retry.")
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def update_item(self, action_item_id: str, **kwargs: Any) -> Optional[ActionItem]:
        item = await self.get_by_id(action_item_id)
        if not item:
            return None
        if "status" in kwargs and not action_item_transition_allowed(item.status, kwargs["status"]):
            target = kwargs["status"]
            raise ValueError(
                f"Action item cannot transition from '{item.status.value}' to '{target.value}'."
            )
        if "deadline" in kwargs:
            kwargs["deadline"] = parse_deadline_datetime(kwargs["deadline"])
        values = {key: value for key, value in kwargs.items() if hasattr(item, key)}
        if "status" in values:
            result = await self.session.execute(
                update(ActionItem)
                .where(ActionItem.id == item.id, ActionItem.status == item.status)
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                await self.session.rollback()
                raise ValueError("Action item status changed concurrently; reload it and retry.")
        else:
            for key, value in values.items():
                setattr(item, key, value)
        await self.session.commit()
        await self.session.refresh(item)
        return item


class ApprovalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        action_type: str,
        description: str,
        justification: str,
        requested_by: str,
        details: Optional[dict[str, Any]] = None,
        status: ApprovalStatus = ApprovalStatus.PENDING
    ) -> Approval:
        approval = Approval(
            id=str(uuid.uuid4()),
            action_type=action_type,
            description=description,
            justification=justification,
            requested_by=requested_by,
            details=details or {},
            status=status,
            requested_at=utc_now()
        )
        self.session.add(approval)
        await self.session.commit()
        await self.session.refresh(approval)
        return approval

    async def get_by_id(self, approval_id: str) -> Optional[Approval]:
        stmt = select(Approval).where(Approval.id == approval_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_pending(self) -> Sequence[Approval]:
        stmt = select(Approval).where(Approval.status == ApprovalStatus.PENDING).order_by(Approval.requested_at.asc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_all(self) -> Sequence[Approval]:
        stmt = select(Approval).order_by(Approval.requested_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def decide(self, approval_id: str, decision: str, decided_by: str) -> Optional[Approval]:
        async with get_session_lock(self.session):
            approval = await self.session.scalar(
                select(Approval).where(Approval.id == approval_id).with_for_update()
            )
            if not approval or approval.status != ApprovalStatus.PENDING:
                await self.session.rollback()
                return None
            if decided_by == approval.requested_by:
                await self.session.rollback()
                raise ValueError("The approval requester cannot approve or reject their own request.")
            normalized = decision.lower()
            if normalized not in {"approved", "rejected"}:
                raise ValueError("Approval decision must be approved or rejected.")
            approval.status = ApprovalStatus.APPROVED if normalized == "approved" else ApprovalStatus.REJECTED
            approval.decided_by = decided_by
            approval.decided_at = utc_now()
            await self.session.commit()
            await self.session.refresh(approval)
            return approval

    async def consume_once(
        self,
        approval_id: str,
        action_type: str,
        expected_details: dict[str, Any],
        consumed_by: str,
    ) -> Optional[Approval]:
        async with get_session_lock(self.session):
            approval = await self.session.scalar(
                select(Approval).where(Approval.id == approval_id).with_for_update()
            )
            if (
                not approval
                or approval.status != ApprovalStatus.APPROVED
                or approval.consumed_at is not None
                or approval.action_type != action_type
                or any(approval.details.get(key) != value for key, value in expected_details.items())
            ):
                await self.session.rollback()
                return None
            approval.consumed_at = utc_now()
            approval.consumed_by = consumed_by
            await self.session.commit()
            await self.session.refresh(approval)
            return approval



class AuditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_entry(
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any],
        ip: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        request_hash: Optional[str] = None,
    ) -> AuditLog:
        """Stage a hash-chained record in the current transaction without committing."""
        actual_payload = payload if payload is not None else {}
        actual_details = details if details is not None else {}
        actual_request_hash = request_hash
        if not actual_request_hash:
            if actual_payload and actual_details:
                target = {"details": actual_details, "payload": actual_payload}
            else:
                target = actual_payload or actual_details
            payload_str = json.dumps(target, sort_keys=True, default=str)
            actual_request_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        # Fetch last entry for cryptographic chain link
        last_stmt = select(AuditLog).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(1)
        last_res = await self.session.execute(last_stmt)
        last_entry = last_res.scalars().first()
        prev_hash = last_entry.signature if (last_entry and last_entry.signature) else "GENESIS_AUDIT_BLOCK"
        timestamp = datetime.now(timezone.utc).replace(tzinfo=None)
        if last_entry and last_entry.timestamp and timestamp <= last_entry.timestamp:
            # Keep append order deterministic when the database clock has
            # equal or lower precision than rapid consecutive audit writes.
            timestamp = last_entry.timestamp + timedelta(microseconds=1)

        # Compute cryptographic signature
        sig_raw = f"{prev_hash}:{actor}:{action}:{resource_type}:{resource_id}:{actual_request_hash}"
        signature = hashlib.sha256(sig_raw.encode("utf-8")).hexdigest()

        entry = AuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip,
            request_hash=actual_request_hash,
            payload=actual_payload,
            signature=signature,
            previous_hash=prev_hash,
            timestamp=timestamp,
        )
        self.session.add(entry)
        return entry

    async def log(
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, Any],
        ip: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        request_hash: Optional[str] = None,
    ) -> AuditLog:
        entry = await self.create_entry(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip=ip,
            payload=payload,
            request_hash=request_hash,
        )
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def list_all(self, limit: int = 100, offset: int = 0) -> Sequence[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).offset(offset).limit(limit)
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def list_by_actor(self, actor: str, limit: int = 100) -> Sequence[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.actor == actor).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def list_by_action(self, action: str, limit: int = 100) -> Sequence[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def count(self) -> int:
        stmt = select(AuditLog)
        res = await self.session.execute(stmt)
        return len(res.scalars().all())

    async def verify_chain_integrity(self) -> tuple[bool, list[str]]:
        """
        Cryptographically verifies the SHA-256 tamper-evident chain of all audit entries.
        Detects any record modification, deletion, or sequence tampering.
        """
        stmt = select(AuditLog)
        res = await self.session.execute(stmt)
        records = list(res.scalars().all())

        if not records:
            return True, []

        from neural.observability.audit import AuditLogChainVerifier

        ordered = AuditLogChainVerifier.order_chain(records)
        return AuditLogChainVerifier.verify_records(ordered)


AuditLogRepository = AuditRepository



class PerformanceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        employee_id: str,
        task_id: str,
        completion_time_seconds: float = 0.0,
        retry_count: int = 0,
        quality_score: float = 1.0,
        cost_tokens: int = 0,
        cost_usd: float = 0.0,
        outcome: str = "success"
    ) -> PerformanceRecord:
        lock = get_session_lock(self.session)
        async with lock:
            # Resolve employee UUID if public employee_id was provided
            stmt_emp = select(Employee).where(
                (Employee.employee_id == employee_id) | (Employee.id == employee_id)
            )
            res = await self.session.execute(stmt_emp)
            emp = res.scalar_one_or_none()
            emp_uuid = emp.id if emp else employee_id

            entry = PerformanceRecord(
                id=str(uuid.uuid4()),
                employee_id=emp_uuid,
                task_id=task_id,
                completion_time_seconds=completion_time_seconds,
                retry_count=retry_count,
                quality_score=quality_score,
                cost_tokens=cost_tokens,
                cost_usd=cost_usd,
                outcome=outcome
            )
            self.session.add(entry)
            await self.session.commit()
            await self.session.refresh(entry)
            return entry

    async def list_by_employee(self, employee_id: str) -> Sequence[PerformanceRecord]:
        stmt_emp = select(Employee).where(
            (Employee.employee_id == employee_id) | (Employee.id == employee_id)
        )
        res = await self.session.execute(stmt_emp)
        emp = res.scalar_one_or_none()
        emp_uuid = emp.id if emp else employee_id

        stmt = (
            select(PerformanceRecord)
            .where(PerformanceRecord.employee_id == emp_uuid)
            .order_by(PerformanceRecord.recorded_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def list_by_task(self, task_id: str) -> Sequence[PerformanceRecord]:
        stmt = (
            select(PerformanceRecord)
            .where(PerformanceRecord.task_id == task_id)
            .order_by(PerformanceRecord.recorded_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def list_all(self) -> Sequence[PerformanceRecord]:
        stmt = select(PerformanceRecord).order_by(PerformanceRecord.recorded_at.desc())
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def get_total_cost(self) -> dict[str, Any]:
        records = await self.list_all()
        total_tokens = sum(r.cost_tokens for r in records)
        total_usd = sum(r.cost_usd for r in records)
        return {
            "total_tokens": total_tokens,
            "total_usd": round(total_usd, 6),
            "records_count": len(records)
        }


class ArtifactRepository:
    """
    Repository for managing task deliverables, code artifacts, test logs,
    and configurations with version history and SHA-256 checksum tracking.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        project_id: str,
        name: str,
        artifact_type: str,
        path: str,
        checksum: str,
        version: int = 1,
        task_id: Optional[str] = None
    ) -> Artifact:
        t_uuid = None
        if task_id:
            stmt_t = select(Task).where((Task.task_id == task_id) | (Task.id == task_id))
            res_t = await self.session.execute(stmt_t)
            t = res_t.scalar_one_or_none()
            t_uuid = t.id if t else task_id

        # Resolve project_id to UUID if needed
        p_uuid = project_id
        stmt_p = select(Project).where((Project.id == project_id) | (Project.name == project_id))
        res_p = await self.session.execute(stmt_p)
        p = res_p.scalar_one_or_none()
        if p:
            p_uuid = p.id

        artifact = Artifact(
            id=str(uuid.uuid4()),
            project_id=p_uuid,
            task_id=t_uuid,
            name=name,
            artifact_type=artifact_type,
            path=path,
            checksum=checksum,
            version=version,
            created_at=utc_now()
        )
        lock = get_session_lock(self.session)
        async with lock:
            self.session.add(artifact)
            await self.session.commit()
            await self.session.refresh(artifact)
        return artifact

    async def get_by_id(self, artifact_id: str) -> Optional[Artifact]:
        stmt = select(Artifact).where(Artifact.id == artifact_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name(
        self,
        project_id: str,
        name: str,
        version: Optional[int] = None
    ) -> Optional[Artifact]:
        stmt_p = select(Project).where((Project.id == project_id) | (Project.name == project_id))
        res_p = await self.session.execute(stmt_p)
        p = res_p.scalar_one_or_none()
        p_uuid = p.id if p else project_id

        stmt = select(Artifact).where(
            Artifact.project_id == p_uuid,
            Artifact.name == name
        )
        if version is not None:
            stmt = stmt.where(Artifact.version == version)
        else:
            stmt = stmt.order_by(Artifact.version.desc())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_latest_version(self, project_id: str, name: str) -> int:
        stmt_p = select(Project).where((Project.id == project_id) | (Project.name == project_id))
        res_p = await self.session.execute(stmt_p)
        p = res_p.scalar_one_or_none()
        p_uuid = p.id if p else project_id

        stmt = select(Artifact.version).where(
            Artifact.project_id == p_uuid,
            Artifact.name == name
        ).order_by(Artifact.version.desc())
        result = await self.session.execute(stmt)
        latest = result.scalars().first()
        return latest or 0

    async def list_by_project(
        self,
        project_id: str,
        artifact_type: Optional[str] = None
    ) -> Sequence[Artifact]:
        stmt_p = select(Project).where((Project.id == project_id) | (Project.name == project_id))
        res_p = await self.session.execute(stmt_p)
        p = res_p.scalar_one_or_none()
        p_uuid = p.id if p else project_id

        stmt = select(Artifact).where(Artifact.project_id == p_uuid)
        if artifact_type:
            stmt = stmt.where(Artifact.artifact_type == artifact_type)
        stmt = stmt.order_by(Artifact.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_by_task(self, task_id: str) -> Sequence[Artifact]:
        stmt_t = select(Task).where((Task.task_id == task_id) | (Task.id == task_id))
        res_t = await self.session.execute(stmt_t)
        t = res_t.scalar_one_or_none()
        t_uuid = t.id if t else task_id
        stmt = select(Artifact).where(Artifact.task_id == t_uuid).order_by(Artifact.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_versions(self, project_id: str, name: str) -> Sequence[Artifact]:
        stmt_p = select(Project).where((Project.id == project_id) | (Project.name == project_id))
        res_p = await self.session.execute(stmt_p)
        p = res_p.scalar_one_or_none()
        p_uuid = p.id if p else project_id

        stmt = select(Artifact).where(
            Artifact.project_id == p_uuid,
            Artifact.name == name
        ).order_by(Artifact.version.asc())
        result = await self.session.execute(stmt)
        return result.scalars().all()


class MessageRepository:
    """
    Persistent audit trail, optionally paired with an outbox delivery record.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_message(
        self,
        message: Any,
        channel: Optional[str] = None,
        delivery_payload: Optional[str] = None,
    ) -> Message:
        if hasattr(message, "model_dump"):
            data = message.model_dump()
        elif isinstance(message, dict):
            data = dict(message)
        else:
            data = {"content": str(message)}

        msg_id = data.get("message_id") or f"MSG-{uuid.uuid4().hex.upper()}"

        existing = await self.get_by_message_id(str(msg_id))
        if existing:
            if delivery_payload is not None:
                pending_outbox = await self.session.execute(
                    select(OutboxEvent.id).where(OutboxEvent.event_id == str(msg_id))
                )
                if pending_outbox.scalar_one_or_none() is None:
                    self.session.add(
                        OutboxEvent(
                            event_id=str(msg_id),
                            channel=str(channel or existing.channel),
                            payload=delivery_payload,
                        )
                    )
                    await self.session.commit()
            return existing

        m_type = data.get("message_type", "direct_message")
        if hasattr(m_type, "value"):
            m_type = m_type.value
        chan = channel or data.get("channel") or "general"
        sender = data.get("sender", "unknown")
        recipient = data.get("recipient")
        department = data.get("department")
        correlation_id = data.get("correlation_id")
        payload = data.get("payload") or data

        msg_obj = Message(
            message_id=str(msg_id),
            message_type=str(m_type),
            channel=str(chan),
            sender=str(sender),
            recipient=str(recipient) if recipient else None,
            department=str(department) if department else None,
            correlation_id=str(correlation_id) if correlation_id else None,
            payload=payload,
        )
        if delivery_payload is not None:
            self.session.add(
                OutboxEvent(
                    event_id=str(msg_id),
                    channel=str(chan),
                    payload=delivery_payload,
                )
            )
        lock = get_session_lock(self.session)
        async with lock:
            self.session.add(msg_obj)
            await self.session.commit()
            await self.session.refresh(msg_obj)
        return msg_obj

    async def get_by_message_id(self, message_id: str) -> Optional[Message]:
        stmt = select(Message).where(Message.message_id == message_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_channel(self, channel: str, limit: int = 100) -> Sequence[Message]:
        stmt = (
            select(Message)
            .where(Message.channel == channel)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_by_employee(self, employee_id: str, limit: int = 100) -> Sequence[Message]:
        stmt = (
            select(Message)
            .where(
                (Message.recipient == employee_id)
                | (Message.sender == employee_id)
                | (Message.channel == f"employee:{employee_id}:inbox")
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_by_department(self, department: str, limit: int = 100) -> Sequence[Message]:
        stmt = (
            select(Message)
            .where(
                (Message.department == department)
                | (Message.channel == f"dept:{department}:broadcast")
                | (Message.channel == f"dept:{department}")
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_recent(self, limit: int = 50) -> Sequence[Message]:
        stmt = select(Message).order_by(Message.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class OutboxRepository:
    """Leased, retryable publication records for the Redis fanout boundary."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def claim_ready(
        self,
        worker_id: str,
        limit: int = 32,
        lease_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        now = utc_now()
        ready = or_(
            and_(OutboxEvent.status == "pending", OutboxEvent.available_at <= now),
            and_(OutboxEvent.status == "leased", OutboxEvent.lease_expires_at <= now),
        )
        stmt = (
            select(OutboxEvent)
            .where(ready)
            .order_by(OutboxEvent.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = (await self.session.execute(stmt)).scalars().all()
        deliveries: list[dict[str, Any]] = []
        async with get_session_lock(self.session):
            for event in events:
                lease_owner = f"{worker_id}:{uuid.uuid4().hex}"
                claimed = await self.session.execute(
                    update(OutboxEvent)
                    .where(OutboxEvent.id == event.id, ready)
                    .values(
                        status="leased",
                        lease_owner=lease_owner,
                        lease_expires_at=now + timedelta(seconds=lease_seconds),
                        attempt_count=OutboxEvent.attempt_count + 1,
                    )
                )
                if claimed.rowcount == 1:
                    deliveries.append(
                        {
                            "event_id": event.event_id,
                            "channel": event.channel,
                            "payload": event.payload,
                            "lease_owner": lease_owner,
                            "attempt_count": event.attempt_count + 1,
                        }
                    )
            await self.session.commit()
        return deliveries

    async def mark_delivered(self, event_id: str, lease_owner: str) -> bool:
        async with get_session_lock(self.session):
            result = await self.session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.event_id == event_id,
                    OutboxEvent.status == "leased",
                    OutboxEvent.lease_owner == lease_owner,
                )
                .values(
                    status="delivered",
                    delivered_at=utc_now(),
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error=None,
                )
            )
            await self.session.commit()
        return result.rowcount == 1

    async def record_failure(
        self,
        event_id: str,
        lease_owner: str,
        error: str,
        retry_delay_seconds: int,
        max_attempts: int,
    ) -> Optional[str]:
        async with get_session_lock(self.session):
            event = (
                await self.session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_id == event_id,
                        OutboxEvent.status == "leased",
                        OutboxEvent.lease_owner == lease_owner,
                    )
                )
            ).scalar_one_or_none()
            if event is None:
                return None
            next_status = "failed" if event.attempt_count >= max_attempts else "pending"
            result = await self.session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event.id,
                    OutboxEvent.status == "leased",
                    OutboxEvent.lease_owner == lease_owner,
                )
                .values(
                    status=next_status,
                    available_at=utc_now() + timedelta(seconds=retry_delay_seconds),
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error=error[:4000],
                )
            )
            await self.session.commit()
        return next_status if result.rowcount == 1 else None


def compute_ledger_entry_hash(
    sequence_number: int,
    proposal_id: str,
    decision: str,
    previous_hash: str,
    recorded_at_iso: str,
    votes_summary: dict[str, Any],
    rationale: str
) -> str:
    serialized_votes = json.dumps(votes_summary, sort_keys=True)
    payload = f"{sequence_number}|{proposal_id}|{decision}|{previous_hash}|{recorded_at_iso}|{serialized_votes}|{rationale}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ProposalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        proposal_id: str,
        title: str,
        description: str,
        proposal_type: ProposalType = ProposalType.STRATEGIC_INITIATIVE,
        status: ProposalStatus = ProposalStatus.DRAFT,
        proposer_id: str = "BossAI",
        proposer_role: str = "Chief Architect / CEO",
        project_id: Optional[str] = None,
        parameters: Optional[dict[str, Any]] = None,
        quorum_threshold: float = 0.5,
        approval_threshold: float = 0.5
    ) -> Proposal:
        p_uuid = None
        if project_id:
            stmt_p = select(Project).where((Project.id == project_id) | (Project.name == project_id))
            res_p = await self.session.execute(stmt_p)
            p = res_p.scalar_one_or_none()
            p_uuid = p.id if p else project_id

        prop = Proposal(
            id=str(uuid.uuid4()),
            proposal_id=proposal_id,
            title=title,
            description=description,
            proposal_type=proposal_type,
            status=status,
            proposer_id=proposer_id,
            proposer_role=proposer_role,
            project_id=p_uuid,
            parameters=parameters or {},
            deliberation_history=[],
            quorum_threshold=quorum_threshold,
            approval_threshold=approval_threshold,
            created_at=utc_now(),
            updated_at=utc_now()
        )
        lock = get_session_lock(self.session)
        async with lock:
            self.session.add(prop)
            await self.session.commit()
            await self.session.refresh(prop)
        return prop

    async def get_by_id(self, id: str) -> Optional[Proposal]:
        stmt = (
            select(Proposal)
            .where(Proposal.id == id)
            .options(
                selectinload(Proposal.votes),
                selectinload(Proposal.ledger_entries)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_proposal_id(self, proposal_id: str) -> Optional[Proposal]:
        stmt = (
            select(Proposal)
            .where((Proposal.proposal_id == proposal_id) | (Proposal.id == proposal_id))
            .options(
                selectinload(Proposal.votes),
                selectinload(Proposal.ledger_entries)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self,
        status: Optional[ProposalStatus] = None,
        project_id: Optional[str] = None,
        limit: int = 100
    ) -> Sequence[Proposal]:
        stmt = (
            select(Proposal)
            .options(
                selectinload(Proposal.votes),
                selectinload(Proposal.ledger_entries)
            )
            .order_by(Proposal.created_at.desc())
        )
        if status:
            stmt = stmt.where(Proposal.status == status)
        if project_id:
            stmt_p = select(Project).where((Project.id == project_id) | (Project.name == project_id))
            res_p = await self.session.execute(stmt_p)
            p = res_p.scalar_one_or_none()
            p_uuid = p.id if p else project_id
            stmt = stmt.where(Proposal.project_id == p_uuid)
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        **kwargs: Any
    ) -> Optional[Proposal]:
        prop = await self.get_by_proposal_id(proposal_id)
        if not prop:
            return None
        prop.status = status
        prop.updated_at = utc_now()
        for k, v in kwargs.items():
            if hasattr(prop, k):
                setattr(prop, k, v)
        lock = get_session_lock(self.session)
        async with lock:
            await self.session.commit()
            await self.session.refresh(prop)
        return prop

    async def add_deliberation_statement(
        self,
        proposal_id: str,
        statement: dict[str, Any]
    ) -> Optional[Proposal]:
        prop = await self.get_by_proposal_id(proposal_id)
        if not prop:
            return None
        history = list(prop.deliberation_history or [])
        statement_with_time = dict(statement)
        if "timestamp" not in statement_with_time:
            statement_with_time["timestamp"] = utc_now().isoformat()
        history.append(statement_with_time)
        prop.deliberation_history = history
        prop.updated_at = utc_now()
        lock = get_session_lock(self.session)
        async with lock:
            await self.session.commit()
            await self.session.refresh(prop)
        return prop

    async def add_deliberation_statements(
        self,
        proposal_id: str,
        statements: Sequence[dict[str, Any]]
    ) -> Optional[Proposal]:
        prop = await self.get_by_proposal_id(proposal_id)
        if not prop:
            return None
        history = list(prop.deliberation_history or [])
        now_iso = utc_now().isoformat()
        for stmt in statements:
            stmt_with_time = dict(stmt)
            if "timestamp" not in stmt_with_time:
                stmt_with_time["timestamp"] = now_iso
            history.append(stmt_with_time)
        prop.deliberation_history = history
        prop.updated_at = utc_now()
        lock = get_session_lock(self.session)
        async with lock:
            await self.session.commit()
            await self.session.refresh(prop)
        return prop

    async def set_execution_receipt(
        self,
        proposal_id: str,
        receipt: dict[str, Any],
        project_id: Optional[str] = None
    ) -> Optional[Proposal]:
        prop = await self.get_by_proposal_id(proposal_id)
        if not prop:
            return None
        prop.execution_receipt = receipt
        prop.status = ProposalStatus.EXECUTED
        prop.executed_at = utc_now()
        prop.updated_at = utc_now()
        if project_id:
            prop.project_id = project_id
        lock = get_session_lock(self.session)
        async with lock:
            await self.session.commit()
            await self.session.refresh(prop)
        return prop


class VoteRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def cast_vote(
        self,
        proposal_id: str,
        voter_id: str,
        voter_name: str,
        voter_role: str,
        department: str,
        choice: VoteChoice,
        weight: float = 1.0,
        rationale: Optional[str] = None
    ) -> Vote:
        stmt_p = select(Proposal).where((Proposal.proposal_id == proposal_id) | (Proposal.id == proposal_id))
        res_p = await self.session.execute(stmt_p)
        prop = res_p.scalar_one_or_none()
        p_uuid = prop.id if prop else proposal_id

        lock = get_session_lock(self.session)
        async with lock:
            stmt_v = select(Vote).where(Vote.proposal_id == p_uuid, Vote.voter_id == voter_id)
            res_v = await self.session.execute(stmt_v)
            existing_vote = res_v.scalar_one_or_none()

            if existing_vote:
                existing_vote.choice = choice
                existing_vote.voter_name = voter_name
                existing_vote.voter_role = voter_role
                existing_vote.department = department
                existing_vote.weight = weight
                existing_vote.rationale = rationale
                existing_vote.voted_at = utc_now()
                await self.session.commit()
                await self.session.refresh(existing_vote)
                return existing_vote
            else:
                vote = Vote(
                    id=str(uuid.uuid4()),
                    proposal_id=p_uuid,
                    voter_id=voter_id,
                    voter_name=voter_name,
                    voter_role=voter_role,
                    department=department,
                    choice=choice,
                    weight=weight,
                    rationale=rationale,
                    voted_at=utc_now()
                )
                self.session.add(vote)
                await self.session.commit()
                await self.session.refresh(vote)
                return vote

    async def list_by_proposal(self, proposal_id: str) -> Sequence[Vote]:
        stmt_p = select(Proposal).where((Proposal.proposal_id == proposal_id) | (Proposal.id == proposal_id))
        res_p = await self.session.execute(stmt_p)
        prop = res_p.scalar_one_or_none()
        p_uuid = prop.id if prop else proposal_id

        stmt = select(Vote).where(Vote.proposal_id == p_uuid).order_by(Vote.voted_at.asc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_vote(self, proposal_id: str, voter_id: str) -> Optional[Vote]:
        stmt_p = select(Proposal).where((Proposal.proposal_id == proposal_id) | (Proposal.id == proposal_id))
        res_p = await self.session.execute(stmt_p)
        prop = res_p.scalar_one_or_none()
        p_uuid = prop.id if prop else proposal_id

        stmt = select(Vote).where(Vote.proposal_id == p_uuid, Vote.voter_id == voter_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class DecisionLedgerRepository:
    _ledger_lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, session: AsyncSession):
        self.session = session

    async def append_entry(
        self,
        proposal_id: str,
        decision: str,
        title: str,
        proposal_type: str,
        proposer_id: str,
        votes_summary: dict[str, Any],
        rationale: str,
        execution_receipt: Optional[dict[str, Any]] = None
    ) -> DecisionLedgerEntry:
        stmt_p = select(Proposal).where((Proposal.proposal_id == proposal_id) | (Proposal.id == proposal_id))
        res_p = await self.session.execute(stmt_p)
        prop = res_p.scalar_one_or_none()
        p_uuid = prop.id if prop else proposal_id
        prop_id_str = prop.proposal_id if prop else proposal_id

        async with self._ledger_lock:
            stmt_latest = select(DecisionLedgerEntry).order_by(DecisionLedgerEntry.sequence_number.desc()).limit(1)
            res_latest = await self.session.execute(stmt_latest)
            latest_entry = res_latest.scalar_one_or_none()

            if latest_entry:
                seq_num = latest_entry.sequence_number + 1
                prev_hash = latest_entry.entry_hash
            else:
                seq_num = 1
                prev_hash = "0" * 64

            now_dt = utc_now()
            now_iso = now_dt.isoformat()
            entry_hash = compute_ledger_entry_hash(
                sequence_number=seq_num,
                proposal_id=prop_id_str,
                decision=decision,
                previous_hash=prev_hash,
                recorded_at_iso=now_iso,
                votes_summary=votes_summary,
                rationale=rationale
            )

            entry_id = f"LEDGER-{uuid.uuid4().hex[:8].upper()}"
            entry = DecisionLedgerEntry(
                id=str(uuid.uuid4()),
                entry_id=entry_id,
                proposal_id=p_uuid,
                sequence_number=seq_num,
                decision=decision,
                title=title,
                proposal_type=proposal_type,
                proposer_id=proposer_id,
                votes_summary=votes_summary,
                rationale=rationale,
                execution_receipt=execution_receipt,
                previous_hash=prev_hash,
                entry_hash=entry_hash,
                recorded_at=now_dt
            )
            self.session.add(entry)
            await self.session.commit()
            await self.session.refresh(entry)
            return entry

    async def update_execution_receipt(
        self,
        proposal_id: str,
        receipt: dict[str, Any]
    ) -> Optional[DecisionLedgerEntry]:
        stmt_p = select(Proposal).where((Proposal.proposal_id == proposal_id) | (Proposal.id == proposal_id))
        res_p = await self.session.execute(stmt_p)
        prop = res_p.scalar_one_or_none()
        p_uuid = prop.id if prop else proposal_id

        async with self._ledger_lock:
            stmt = (
                select(DecisionLedgerEntry)
                .where(DecisionLedgerEntry.proposal_id == p_uuid)
                .order_by(DecisionLedgerEntry.sequence_number.desc())
                .limit(1)
            )
            res = await self.session.execute(stmt)
            entry = res.scalar_one_or_none()
            if not entry:
                return None
            entry.execution_receipt = receipt
            await self.session.commit()
            await self.session.refresh(entry)
            return entry

    async def get_by_entry_id(self, entry_id: str) -> Optional[DecisionLedgerEntry]:
        stmt = (
            select(DecisionLedgerEntry)
            .where((DecisionLedgerEntry.entry_id == entry_id) | (DecisionLedgerEntry.id == entry_id))
            .options(selectinload(DecisionLedgerEntry.proposal))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_proposal_id(self, proposal_id: str) -> Optional[DecisionLedgerEntry]:
        stmt_p = select(Proposal).where((Proposal.proposal_id == proposal_id) | (Proposal.id == proposal_id))
        res_p = await self.session.execute(stmt_p)
        prop = res_p.scalar_one_or_none()
        p_uuid = prop.id if prop else proposal_id

        stmt = (
            select(DecisionLedgerEntry)
            .where(DecisionLedgerEntry.proposal_id == p_uuid)
            .order_by(DecisionLedgerEntry.sequence_number.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_proposal(self, proposal_id: str) -> Sequence[DecisionLedgerEntry]:
        stmt_p = select(Proposal).where((Proposal.proposal_id == proposal_id) | (Proposal.id == proposal_id))
        res_p = await self.session.execute(stmt_p)
        prop = res_p.scalar_one_or_none()
        p_uuid = prop.id if prop else proposal_id

        stmt = (
            select(DecisionLedgerEntry)
            .where(DecisionLedgerEntry.proposal_id == p_uuid)
            .order_by(DecisionLedgerEntry.sequence_number.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_all(self, limit: int = 100) -> Sequence[DecisionLedgerEntry]:
        stmt = select(DecisionLedgerEntry).order_by(DecisionLedgerEntry.sequence_number.asc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def verify_chain_integrity(self) -> tuple[bool, Optional[str]]:
        stmt = (
            select(DecisionLedgerEntry)
            .options(selectinload(DecisionLedgerEntry.proposal))
            .order_by(DecisionLedgerEntry.sequence_number.asc())
        )
        result = await self.session.execute(stmt)
        entries = result.scalars().all()

        if not entries:
            return True, None

        expected_prev_hash = "0" * 64
        for idx, entry in enumerate(entries, start=1):
            if entry.sequence_number != idx:
                return False, f"Sequence gap or mismatch at index {idx}: entry has sequence_number {entry.sequence_number}"

            if entry.previous_hash != expected_prev_hash:
                return False, f"Broken hash link at sequence {entry.sequence_number}: expected prev_hash {expected_prev_hash}, got {entry.previous_hash}"

            prop_id_str = entry.proposal.proposal_id if entry.proposal else entry.proposal_id
            recomputed_hash = compute_ledger_entry_hash(
                sequence_number=entry.sequence_number,
                proposal_id=prop_id_str,
                decision=entry.decision,
                previous_hash=entry.previous_hash,
                recorded_at_iso=entry.recorded_at.isoformat(),
                votes_summary=entry.votes_summary,
                rationale=entry.rationale
            )
            if entry.entry_hash != recomputed_hash:
                return False, f"Entry hash mismatch at sequence {entry.sequence_number}: stored {entry.entry_hash}, recomputed {recomputed_hash}"

            expected_prev_hash = entry.entry_hash

        return True, None


def compute_certificate_hash(
    certificate_id: str,
    employee_id: str,
    skill_name: str,
    level_str: str,
    score: float,
    benchmark_id: str,
    issued_at_iso: str
) -> str:
    clean_iso = str(issued_at_iso).replace("+00:00", "").replace("Z", "").replace(" ", "T")
    if "." in clean_iso:
        clean_iso = clean_iso.split(".")[0]
    payload = {
        "benchmark_id": benchmark_id,
        "certificate_id": certificate_id,
        "employee_id": employee_id,
        "issued_at": clean_iso,
        "level": level_str,
        "score": round(score, 4),
        "skill_name": skill_name,
    }
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SkillCertificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_certification(
        self,
        certificate_id: str,
        employee_id: str,
        skill_name: str,
        level: SkillLevel,
        score: float,
        benchmark_id: str,
        execution_receipt: Optional[dict[str, Any]] = None,
        rubric_scores: Optional[dict[str, Any]] = None,
        certificate_hash: Optional[str] = None,
        status: str = "certified",
        issued_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None
    ) -> SkillCertification:
        now = issued_at or utc_now()
        if hasattr(now, "tzinfo") and now.tzinfo is not None:
            now = now.replace(tzinfo=None)
        level_str = level.value if hasattr(level, "value") else str(level)
        h = certificate_hash or compute_certificate_hash(
            certificate_id=certificate_id,
            employee_id=employee_id,
            skill_name=skill_name,
            level_str=level_str,
            score=score,
            benchmark_id=benchmark_id,
            issued_at_iso=now.isoformat()
        )

        cert = SkillCertification(
            certificate_id=certificate_id,
            employee_id=employee_id,
            skill_name=skill_name,
            level=level,
            score=score,
            benchmark_id=benchmark_id,
            execution_receipt=execution_receipt or {},
            rubric_scores=rubric_scores or {},
            certificate_hash=h,
            status=status,
            issued_at=now,
            expires_at=expires_at
        )
        self.session.add(cert)
        await self.session.commit()
        await self.session.refresh(cert)
        return cert

    async def get_by_certificate_id(self, certificate_id: str) -> Optional[SkillCertification]:
        stmt = (
            select(SkillCertification)
            .where(SkillCertification.certificate_id == certificate_id)
            .options(selectinload(SkillCertification.employee))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_employee_id(self, employee_id: str) -> Sequence[SkillCertification]:
        # Handle lookup either by Employee.employee_id (e.g. ANC-EMP-0001) or Employee.id (UUID)
        stmt_emp = select(Employee).where(Employee.employee_id == employee_id)
        res_emp = await self.session.execute(stmt_emp)
        emp = res_emp.scalar_one_or_none()
        target_id = emp.id if emp else employee_id

        stmt = (
            select(SkillCertification)
            .where(SkillCertification.employee_id == target_id)
            .order_by(SkillCertification.issued_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_all(
        self,
        skill_name: Optional[str] = None,
        employee_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> Sequence[SkillCertification]:
        stmt = select(SkillCertification)
        if skill_name:
            stmt = stmt.where(SkillCertification.skill_name.ilike(skill_name))
        if employee_id:
            stmt_emp = select(Employee).where(Employee.employee_id == employee_id)
            res_emp = await self.session.execute(stmt_emp)
            emp = res_emp.scalar_one_or_none()
            target_id = emp.id if emp else employee_id
            stmt = stmt.where(SkillCertification.employee_id == target_id)
        if status:
            stmt = stmt.where(SkillCertification.status == status)
        stmt = stmt.order_by(SkillCertification.issued_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def verify_certificate_integrity(self, certificate_id: str) -> tuple[bool, str]:
        cert = await self.get_by_certificate_id(certificate_id)
        if not cert:
            return False, f"Certificate {certificate_id} not found"
        level_str = cert.level.value if hasattr(cert.level, "value") else str(cert.level)
        issued_at_iso = cert.issued_at.isoformat() if hasattr(cert.issued_at, "isoformat") else str(cert.issued_at)
        expected_hash = compute_certificate_hash(
            certificate_id=cert.certificate_id,
            employee_id=cert.employee_id,
            skill_name=cert.skill_name,
            level_str=level_str,
            score=cert.score,
            benchmark_id=cert.benchmark_id,
            issued_at_iso=issued_at_iso
        )
        is_valid = cert.certificate_hash == expected_hash
        msg = "Cryptographic integrity verified" if is_valid else f"Hash mismatch: stored {cert.certificate_hash}, expected {expected_hash}"
        return is_valid, msg


class EvolutionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_record(
        self,
        record_type: str,
        hypothesis: str,
        experiment_details: Optional[dict[str, Any]] = None,
        baseline_metrics: Optional[dict[str, Any]] = None,
        experiment_metrics: Optional[dict[str, Any]] = None,
        status: str = "in_review",
        version: str = "1.0.0"
    ) -> EvolutionRecord:
        record = EvolutionRecord(
            record_type=record_type,
            hypothesis=hypothesis,
            experiment_details=experiment_details or {},
            baseline_metrics=baseline_metrics or {},
            experiment_metrics=experiment_metrics or {},
            status=status,
            version=version
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get_by_id(self, record_id: str) -> Optional[EvolutionRecord]:
        stmt = select(EvolutionRecord).where(EvolutionRecord.id == record_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self,
        record_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50
    ) -> Sequence[EvolutionRecord]:
        stmt = select(EvolutionRecord)
        if record_type:
            stmt = stmt.where(EvolutionRecord.record_type == record_type)
        if status:
            stmt = stmt.where(EvolutionRecord.status == status)
        stmt = stmt.order_by(EvolutionRecord.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_status(
        self,
        record_id: str,
        status: str,
        experiment_metrics: Optional[dict[str, Any]] = None
    ) -> Optional[EvolutionRecord]:
        record = await self.get_by_id(record_id)
        if record:
            record.status = status
            if experiment_metrics:
                m = dict(record.experiment_metrics or {})
                m.update(experiment_metrics)
                record.experiment_metrics = m
            await self.session.commit()
            await self.session.refresh(record)
        return record


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_entry(
        self,
        category: MemoryCategory,
        source: str,
        content: str,
        metadata_json: Optional[dict[str, Any]] = None,
        confidence: float = 1.0,
        embedding_ref: Optional[str] = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            category=category,
            source=source,
            content=content,
            metadata_json=metadata_json or {},
            confidence=confidence,
            embedding_ref=embedding_ref,
        )
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def get_by_id(self, entry_id: str) -> Optional[MemoryEntry]:
        stmt = select(MemoryEntry).where(MemoryEntry.id == entry_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_entries(
        self,
        category: Optional[MemoryCategory] = None,
        source: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[MemoryEntry]:
        stmt = select(MemoryEntry)
        if category:
            stmt = stmt.where(MemoryEntry.category == category)
        if source:
            stmt = stmt.where(MemoryEntry.source == source)
        if search:
            stmt = stmt.where(MemoryEntry.content.ilike(f"%{search}%"))
        stmt = stmt.order_by(MemoryEntry.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cat in MemoryCategory:
            stmt = select(MemoryEntry).where(MemoryEntry.category == cat)
            res = await self.session.execute(stmt)
            counts[cat.value] = len(res.scalars().all())
        return counts


class ToolRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_tool(
        self,
        name: str,
        code: str,
        test_code: str,
        description: str = "",
        version: str = "1.0.0",
        category: str = "utility",
        interface_schema: Optional[dict[str, Any]] = None,
        dependencies: Optional[list[str]] = None,
        author_id: str = "autonomous_toolsmith",
        tool_id: Optional[str] = None,
        status: str = "draft",
        safety_status: str = "pending",
        safety_violations: Optional[list[str]] = None,
        verification_receipt: Optional[dict[str, Any]] = None,
    ) -> ToolRecord:
        t_id = tool_id or f"TOOL-{name.upper().replace('_', '-')}-{version.replace('.', '-')}-{uuid.uuid4().hex[:4].upper()}"
        tool = ToolRecord(
            tool_id=t_id,
            name=name,
            version=version,
            description=description,
            category=category,
            code=code,
            test_code=test_code,
            interface_schema=interface_schema or {},
            dependencies=dependencies or [],
            author_id=author_id,
            status=status,
            safety_status=safety_status,
            safety_violations=safety_violations or [],
            verification_receipt=verification_receipt or {},
        )
        self.session.add(tool)
        await self.session.commit()
        await self.session.refresh(tool)
        return tool

    async def get_by_id(self, id: str) -> Optional[ToolRecord]:
        stmt = select(ToolRecord).where(ToolRecord.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_tool_id(self, tool_id: str) -> Optional[ToolRecord]:
        stmt = select(ToolRecord).where(ToolRecord.tool_id == tool_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_name_and_version(self, name: str, version: str) -> Optional[ToolRecord]:
        stmt = select(ToolRecord).where(
            ToolRecord.name == name,
            ToolRecord.version == version,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_verified_tool(self, name: str) -> Optional[ToolRecord]:
        stmt = (
            select(ToolRecord)
            .where(
                ToolRecord.name == name,
                ToolRecord.status == "verified",
            )
            .order_by(ToolRecord.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_tools(
        self,
        category: Optional[str] = None,
        status: Optional[str] = None,
        author_id: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[ToolRecord]:
        stmt = select(ToolRecord)
        if category:
            stmt = stmt.where(ToolRecord.category == category)
        if status:
            stmt = stmt.where(ToolRecord.status == status)
        if author_id:
            stmt = stmt.where(ToolRecord.author_id == author_id)
        stmt = stmt.order_by(ToolRecord.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_status(
        self,
        id: str,
        status: str,
        verification_receipt: Optional[dict[str, Any]] = None,
        safety_status: Optional[str] = None,
        safety_violations: Optional[list[str]] = None,
    ) -> Optional[ToolRecord]:
        tool = await self.get_by_id(id)
        if tool:
            tool.status = status
            if verification_receipt is not None:
                tool.verification_receipt = verification_receipt
            if safety_status is not None:
                tool.safety_status = safety_status
            if safety_violations is not None:
                tool.safety_violations = safety_violations
            await self.session.commit()
            await self.session.refresh(tool)
        return tool

    async def deprecate_tool(self, tool_id: str) -> Optional[ToolRecord]:
        tool = await self.get_by_tool_id(tool_id)
        if not tool:
            tool = await self.get_by_id(tool_id)
        if tool:
            tool.status = "deprecated"
            await self.session.commit()
            await self.session.refresh(tool)
        return tool


class MethodRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_method(
        self,
        name: str,
        objective: str,
        strategy_type: str,
        steps: list[dict[str, Any]],
        version: str = "1.0.0",
        domain: str = "engineering",
        fallback_strategy: Optional[dict[str, Any]] = None,
        baseline_method_id: Optional[str] = None,
        author_id: str = "MethodLabAI",
        tags: Optional[list[str]] = None,
        method_id: Optional[str] = None,
        status: str = "candidate",
    ) -> MethodRecord:
        m_id = method_id or f"MTH-{name.upper().replace('_', '-')}-{version.replace('.', '-')}-{uuid.uuid4().hex[:4].upper()}"
        method = MethodRecord(
            method_id=m_id,
            name=name,
            version=version,
            domain=domain,
            objective=objective,
            strategy_type=strategy_type,
            steps=steps,
            fallback_strategy=fallback_strategy or {},
            status=status,
            baseline_method_id=baseline_method_id,
            author_id=author_id,
            tags=tags or [],
        )
        self.session.add(method)
        await self.session.commit()
        await self.session.refresh(method)
        return method

    async def get_by_id(self, id: str) -> Optional[MethodRecord]:
        stmt = (
            select(MethodRecord)
            .where(MethodRecord.id == id)
            .options(selectinload(MethodRecord.benchmarks))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_method_id(self, method_id: str) -> Optional[MethodRecord]:
        stmt = (
            select(MethodRecord)
            .where(MethodRecord.method_id == method_id)
            .options(selectinload(MethodRecord.benchmarks))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_methods(
        self,
        status: Optional[str] = None,
        domain: Optional[str] = None,
        strategy_type: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[MethodRecord]:
        stmt = select(MethodRecord).options(selectinload(MethodRecord.benchmarks))
        if status:
            stmt = stmt.where(MethodRecord.status == status)
        if domain:
            stmt = stmt.where(MethodRecord.domain == domain)
        if strategy_type:
            stmt = stmt.where(MethodRecord.strategy_type == strategy_type)
        stmt = stmt.order_by(MethodRecord.created_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_active_catalog(self, domain: Optional[str] = None) -> Sequence[MethodRecord]:
        stmt = (
            select(MethodRecord)
            .where(MethodRecord.status == "promoted")
            .options(selectinload(MethodRecord.benchmarks))
        )
        if domain:
            stmt = stmt.where(MethodRecord.domain == domain)
        stmt = stmt.order_by(MethodRecord.promoted_at.desc().nullslast(), MethodRecord.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def promote_method(self, method_id: str) -> Optional[MethodRecord]:
        method = await self.get_by_method_id(method_id)
        if not method:
            method = await self.get_by_id(method_id)
        if method:
            method.status = "promoted"
            method.promoted_at = utc_now()
            await self.session.commit()
            await self.session.refresh(method)
        return method

    async def update_status(self, id: str, status: str) -> Optional[MethodRecord]:
        method = await self.get_by_id(id)
        if method:
            method.status = status
            if status == "promoted" and not method.promoted_at:
                method.promoted_at = utc_now()
            await self.session.commit()
            await self.session.refresh(method)
        return method


class MethodBenchmarkRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_benchmark(
        self,
        method_id: str,
        total_scenarios: int,
        passed_scenarios: int,
        accuracy: float,
        avg_latency_ms: float,
        token_efficiency: float,
        reproduction_rate: float,
        composite_score: float,
        baseline_method_id: Optional[str] = None,
        baseline_accuracy: Optional[float] = None,
        baseline_latency_ms: Optional[float] = None,
        baseline_token_efficiency: Optional[float] = None,
        relative_improvement_pct: float = 0.0,
        recommendation: str = "iterate",
        scenario_results: Optional[list[dict[str, Any]]] = None,
        benchmark_id: Optional[str] = None,
    ) -> MethodBenchmarkRecord:
        bmk_id = benchmark_id or f"BMK-{uuid.uuid4().hex[:8].upper()}"
        record = MethodBenchmarkRecord(
            benchmark_id=bmk_id,
            method_id=method_id,
            baseline_method_id=baseline_method_id,
            total_scenarios=total_scenarios,
            passed_scenarios=passed_scenarios,
            accuracy=accuracy,
            avg_latency_ms=avg_latency_ms,
            token_efficiency=token_efficiency,
            reproduction_rate=reproduction_rate,
            composite_score=composite_score,
            baseline_accuracy=baseline_accuracy,
            baseline_latency_ms=baseline_latency_ms,
            baseline_token_efficiency=baseline_token_efficiency,
            relative_improvement_pct=relative_improvement_pct,
            recommendation=recommendation,
            scenario_results=scenario_results or [],
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get_by_id(self, id: str) -> Optional[MethodBenchmarkRecord]:
        stmt = select(MethodBenchmarkRecord).where(MethodBenchmarkRecord.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_benchmark_id(self, benchmark_id: str) -> Optional[MethodBenchmarkRecord]:
        stmt = select(MethodBenchmarkRecord).where(MethodBenchmarkRecord.benchmark_id == benchmark_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_method(self, method_id: str) -> Sequence[MethodBenchmarkRecord]:
        stmt = (
            select(MethodBenchmarkRecord)
            .outerjoin(MethodRecord, MethodBenchmarkRecord.method_id == MethodRecord.id)
            .where(
                (MethodBenchmarkRecord.method_id == method_id)
                | (MethodRecord.method_id == method_id)
                | (MethodRecord.name == method_id)
            )
            .order_by(MethodBenchmarkRecord.recorded_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_all(self, limit: int = 50) -> Sequence[MethodBenchmarkRecord]:
        stmt = select(MethodBenchmarkRecord).order_by(MethodBenchmarkRecord.recorded_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class DecisionRecordRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        decision_id: str,
        actor_id: str,
        decision_type: str,
        model_checkpoint: str,
        input_payload: dict[str, Any],
        output_choice: Optional[str] = None,
        probabilities: Optional[dict[str, float]] = None,
        confidence: float = 0.0,
        calibrated_prob: float = 0.0,
        latency_ms: float = 0.0,
        was_escalated: bool = False,
        shadow_mode: bool = True,
        task_id: Optional[str] = None,
        actual_outcome: Optional[str] = None,
    ) -> DecisionRecord:
        record = DecisionRecord(
            decision_id=decision_id,
            actor_id=actor_id,
            task_id=task_id,
            decision_type=decision_type,
            model_checkpoint=model_checkpoint,
            input_payload=input_payload,
            output_choice=output_choice,
            probabilities=probabilities or {},
            confidence=confidence,
            calibrated_prob=calibrated_prob,
            latency_ms=latency_ms,
            was_escalated=was_escalated,
            shadow_mode=shadow_mode,
            actual_outcome=actual_outcome,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get_by_decision_id(self, decision_id: str) -> Optional[DecisionRecord]:
        stmt = select(DecisionRecord).where(DecisionRecord.decision_id == decision_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_task(self, task_id: str) -> Sequence[DecisionRecord]:
        stmt = (
            select(DecisionRecord)
            .where(DecisionRecord.task_id == task_id)
            .order_by(DecisionRecord.timestamp.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_by_actor(self, actor_id: str) -> Sequence[DecisionRecord]:
        stmt = (
            select(DecisionRecord)
            .where(DecisionRecord.actor_id == actor_id)
            .order_by(DecisionRecord.timestamp.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_outcome(self, decision_id: str, actual_outcome: str) -> Optional[DecisionRecord]:
        record = await self.get_by_decision_id(decision_id)
        if not record:
            return None
        record.actual_outcome = actual_outcome
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def list_recent(
        self,
        limit: int = 100,
        shadow_mode: Optional[bool] = None,
        decision_type: Optional[str] = None,
    ) -> Sequence[DecisionRecord]:
        stmt = select(DecisionRecord)
        if shadow_mode is not None:
            stmt = stmt.where(DecisionRecord.shadow_mode == shadow_mode)
        if decision_type is not None:
            stmt = stmt.where(DecisionRecord.decision_type == decision_type)
        stmt = stmt.order_by(DecisionRecord.timestamp.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()







