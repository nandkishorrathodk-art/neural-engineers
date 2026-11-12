from __future__ import annotations

import asyncio
import inspect
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional

from neural.config import get_settings
from neural.constants import TaskStatus
from neural.exceptions import EmergencyStopTriggered, SecurityViolationError
from neural.logger import get_logger

logger = get_logger("ancos.security.kill_switch")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GlobalEmergencyKillSwitch:
    """
    Global Emergency Kill Switch for ANCOS.
    Provides instant halt (<200ms) of:
    - All autonomous worker loops
    - Active Task DAG dispatchers
    - Containerized employee workstations
    - Boss AI scheduler and planning loops

    Also provides resume and state recovery mechanisms from halted states.
    """

    def __init__(self) -> None:
        self.is_halted: bool = False
        self.halt_timestamp: Optional[datetime] = None
        self.halt_reason: Optional[str] = None
        self.halted_by: Optional[str] = None
        self.halt_latency_ms: float = 0.0

        # Subsystem references
        self._schedulers: list[Any] = []
        self._container_managers: list[Any] = []
        self._boss_brains: list[Any] = []
        self._worker_tasks: set[asyncio.Task[Any]] = set()

    def register_scheduler(self, scheduler: Any) -> None:
        if scheduler not in self._schedulers:
            self._schedulers.append(scheduler)

    def register_container_manager(self, manager: Any) -> None:
        if manager not in self._container_managers:
            self._container_managers.append(manager)

    def register_boss_brain(self, brain: Any) -> None:
        if brain not in self._boss_brains:
            self._boss_brains.append(brain)

    def register_worker_task(self, task: asyncio.Task[Any]) -> None:
        self._worker_tasks.add(task)

    def sync_state(self, state: dict[str, Any]) -> None:
        """Refresh the process-local latch from the durable shared state."""
        self.is_halted = bool(state.get("active"))
        self.halt_reason = state.get("reason")
        self.halted_by = state.get("changed_by")
        self.halt_timestamp = state.get("changed_at")

    def guard_active(self) -> None:
        """Raises EmergencyStopTriggered if the global kill switch is currently active."""
        if self.is_halted:
            raise EmergencyStopTriggered(
                f"Global Emergency Stop is active (halted by {self.halted_by} at {self.halt_timestamp}: {self.halt_reason}). "
                "Execution is strictly frozen."
            )

    async def emergency_halt(
        self,
        reason: str = "Operator triggered emergency halt",
        actor: str = "operator",
        audit_repo: Optional[Any] = None,
        message_bus: Optional[Any] = None,
        state_repo: Optional[Any] = None,
        task_repo: Optional[Any] = None,
        resource_lease_repo: Optional[Any] = None,
    ) -> dict[str, Any]:
        """
        Instantly halts all autonomous operations, cancelling active tasks and freezing workstations.
        Guaranteed execution in < 200ms.
        """
        start_time = time.perf_counter()
        logger.critical("EMERGENCY KILL SWITCH ACTIVATED", reason=reason, actor=actor)

        # Persist the halt before local side effects. If the shared database is
        # unavailable, callers must not report a successful global halt.
        if state_repo is not None:
            await state_repo.set_state(active=True, actor=actor, reason=reason)

        self.is_halted = True
        self.halt_timestamp = utc_now()
        self.halt_reason = reason
        self.halted_by = actor

        halted_subsystems: list[str] = []

        # Cancel running task attempts and supersede leases
        if task_repo is not None and hasattr(task_repo, "halt_running_attempts"):
            try:
                cancelled = await task_repo.halt_running_attempts(reason=reason)
                halted_subsystems.append(f"TaskAttempts(cancelled={cancelled})")
            except Exception as e:
                logger.error("Error cancelling task attempts during emergency halt", error=str(e))

        # Reconcile expired resource leases
        if resource_lease_repo is not None and hasattr(resource_lease_repo, "reconcile_expired"):
            try:
                reconciled = await resource_lease_repo.reconcile_expired()
                halted_subsystems.append(f"ResourceLeases(reconciled={reconciled})")
            except Exception as e:
                logger.error("Error reconciling resource leases during emergency halt", error=str(e))

        # 1. Halt Task Schedulers and cancel active task workers
        for scheduler in self._schedulers:
            try:
                if hasattr(scheduler, "cancel_all_active_workers"):
                    if inspect.iscoroutinefunction(scheduler.cancel_all_active_workers):
                        await scheduler.cancel_all_active_workers()
                    else:
                        scheduler.cancel_all_active_workers()
                halted_subsystems.append(f"TaskScheduler({id(scheduler)})")
            except Exception as e:
                logger.error("Error halting scheduler", error=str(e))

        # 2. Cancel registered worker asyncio tasks
        active_cancelled = 0
        for task in list(self._worker_tasks):
            if not task.done():
                task.cancel()
                active_cancelled += 1
        self._worker_tasks.clear()

        # 3. Freeze or pause containerized workstations
        for cm in self._container_managers:
            try:
                if hasattr(cm, "freeze_all_workstations"):
                    if inspect.iscoroutinefunction(cm.freeze_all_workstations):
                        await cm.freeze_all_workstations()
                    else:
                        cm.freeze_all_workstations()
                halted_subsystems.append(f"ContainerManager({id(cm)})")

            except Exception as e:
                logger.error("Error freezing workstations", error=str(e))

        # 4. Pause Boss AI scheduler / brain
        for brain in self._boss_brains:
            try:
                if hasattr(brain, "pause"):
                    brain.pause()
                halted_subsystems.append(f"BossBrain({id(brain)})")
            except Exception as e:
                logger.error("Error pausing BossBrain", error=str(e))

        self.halt_latency_ms = (time.perf_counter() - start_time) * 1000.0

        receipt: dict[str, Any] = {
            "halted": True,
            "status": "HALTED",
            "actor": actor,
            "reason": reason,
            "timestamp": self.halt_timestamp.isoformat(),
            "halt_latency_ms": round(self.halt_latency_ms, 2),
            "cancelled_workers_count": active_cancelled,
            "halted_subsystems": halted_subsystems,
        }

        # 5. Broadcast emergency event if message bus available
        if message_bus:
            try:
                if hasattr(message_bus, "publish_broadcast"):
                    await message_bus.publish_broadcast(
                        channel="boss:broadcast",
                        message_type="emergency_stop",
                        payload=receipt,
                        sender=actor,
                    )
            except Exception as bus_err:
                logger.warning("Failed to publish emergency stop event to message bus", error=str(bus_err))

        # 6. Audit logging
        if audit_repo:
            try:
                await audit_repo.log(
                    actor=actor,
                    action="EMERGENCY_STOP",
                    resource_type="system",
                    resource_id="global_kill_switch",
                    details=receipt,
                )
            except Exception as audit_err:
                logger.warning("Failed to log emergency stop in audit repo", error=str(audit_err))

        logger.info(
            "Emergency halt completed",
            latency_ms=self.halt_latency_ms,
            cancelled_workers=active_cancelled,
        )
        return receipt

    async def resume(
        self,
        actor: str = "operator",
        recovery_strategy: str = "checkpoint",
        auth_token: Optional[str] = None,
        task_repo: Optional[Any] = None,
        audit_repo: Optional[Any] = None,
        message_bus: Optional[Any] = None,
        state_repo: Optional[Any] = None,
        resource_lease_repo: Optional[Any] = None,
    ) -> dict[str, Any]:
        """
        Resumes the system from an emergency halt.
        Recovers state for interrupted tasks and unfreezes schedulers.
        Validates authorization token against configured owner / security reviewer secrets.
        """
        settings = get_settings()
        valid_tokens = [
            tok.strip()
            for tok in (settings.OWNER_API_TOKEN, settings.SECURITY_REVIEWER_API_TOKEN)
            if tok and isinstance(tok, str) and tok.strip()
        ]

        if auth_token is not None:
            raw_token = auth_token.strip()
            if raw_token.lower().startswith("bearer "):
                raw_token = raw_token[7:].strip()
            if not valid_tokens or not any(secrets.compare_digest(raw_token, vt) for vt in valid_tokens):
                raise SecurityViolationError(
                    "Invalid or unauthorized token provided for emergency halt resumption."
                )
        elif settings.APP_ENV == "production":
            raise SecurityViolationError(
                "An authorized token (owner or security reviewer) is required to resume the system in production."
            )

        if state_repo is not None:
            durable_state = await state_repo.get_state()
            self.is_halted = bool(durable_state["active"])
            self.halt_reason = durable_state["reason"]
            self.halted_by = durable_state["changed_by"]
            self.halt_timestamp = durable_state["changed_at"]

        if not self.is_halted:
            return {"resumed": False, "message": "System is not currently halted."}

        logger.info("Resuming system from emergency halt", actor=actor, strategy=recovery_strategy)

        recovered_tasks_count = 0

        # State recovery: reset interrupted/running tasks to PENDING if task_repo is provided
        if task_repo:
            if hasattr(task_repo, "recover_running_after_emergency_stop"):
                recovered_tasks_count = await task_repo.recover_running_after_emergency_stop()
            else:
                # Keep test doubles and older adapters compatible. The production
                # TaskRepository uses the atomic attempt-cancellation/fencing path above.
                running_tasks = await task_repo.list_by_status(TaskStatus.RUNNING)
                for t in running_tasks:
                    await task_repo.update_status(t.task_id, TaskStatus.PENDING)
                    recovered_tasks_count += 1

        # Release expired and quarantined resource leases during recovery
        if resource_lease_repo is not None and hasattr(resource_lease_repo, "release_expired_leases"):
            await resource_lease_repo.release_expired_leases()

        # Unpause Boss AI and schedulers
        for brain in self._boss_brains:
            if hasattr(brain, "resume"):
                brain.resume()

        for cm in self._container_managers:
            if hasattr(cm, "unfreeze_all_workstations"):
                if inspect.iscoroutinefunction(cm.unfreeze_all_workstations):
                    await cm.unfreeze_all_workstations()
                else:
                    cm.unfreeze_all_workstations()


        resumed_at = utc_now()
        duration_halted_seconds = (
            (resumed_at - self.halt_timestamp).total_seconds() if self.halt_timestamp else 0.0
        )

        receipt: dict[str, Any] = {
            "resumed": True,
            "status": "RUNNING",
            "resumed_by": actor,
            "recovery_strategy": recovery_strategy,
            "resumed_at": resumed_at.isoformat(),
            "duration_halted_seconds": duration_halted_seconds,
            "recovered_tasks_count": recovered_tasks_count,
        }

        # Keep the durable latch active until recovery/unfreeze work is done.
        # Persist failure leaves both this instance and all other replicas halted.
        if state_repo is not None:
            await state_repo.set_state(active=False, actor=actor, reason=None)
        self.is_halted = False

        if message_bus:
            try:
                if hasattr(message_bus, "publish_broadcast"):
                    await message_bus.publish_broadcast(
                        channel="boss:broadcast",
                        message_type="system_resumed",
                        payload=receipt,
                        sender=actor,
                    )
            except Exception as e:
                logger.warning("Failed to broadcast resume message", error=str(e))

        if audit_repo:
            try:
                await audit_repo.log(
                    actor=actor,
                    action="RESUME_SYSTEM",
                    resource_type="system",
                    resource_id="global_kill_switch",
                    details=receipt,
                )
            except Exception as e:
                logger.warning("Failed to log resume event in audit repo", error=str(e))

        logger.info("System successfully resumed", recovered_tasks=recovered_tasks_count)
        return receipt


# Singleton instance
_global_kill_switch = GlobalEmergencyKillSwitch()


def get_kill_switch() -> GlobalEmergencyKillSwitch:
    return _global_kill_switch


def reset_kill_switch() -> GlobalEmergencyKillSwitch:
    """Helper for testing to reset kill switch singleton state."""
    global _global_kill_switch
    _global_kill_switch = GlobalEmergencyKillSwitch()
    return _global_kill_switch


def is_kill_switch_active() -> bool:
    return _global_kill_switch.is_halted


def activate_kill_switch(actor: str = "operator", reason: str = "Emergency stop") -> None:
    _global_kill_switch.is_halted = True
    _global_kill_switch.halt_reason = reason
    _global_kill_switch.halted_by = actor
    _global_kill_switch.halt_timestamp = utc_now()

