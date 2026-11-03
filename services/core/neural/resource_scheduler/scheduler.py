from __future__ import annotations

import os
import socket
from typing import Any, Optional

from neural.constants import TaskPriority
from neural.db.repositories import ResourceLeaseRepository
from neural.logger import get_logger
from neural.resource_scheduler.checkpoint_manager import CheckpointManager
from neural.resource_scheduler.desktop_pool import DesktopSlotPool
from neural.resource_scheduler.employee_queue import EmployeeExecutionQueue
from neural.resource_scheduler.hardware_profiler import HardwareProfile, HardwareProfiler
from neural.resource_scheduler.resource_monitor import ResourceMonitor
from neural.resource_scheduler.session_lease import SessionLeaseManager

logger = get_logger("ancos.resource_scheduler")


class DesktopResourceScheduler:
    """
    Desktop Resource Scheduler & Capacity Governor.
    Enforces the fundamental architectural invariant:
    - Many Persistent AI Employees.
    - Active GUI sessions stay within the hardware profile and deployment ceiling.
    - Standby employees retain permanent identity, memory, and state WITHOUT consuming running containers.
    - Dynamic admission control, priority queueing, durable checkpointing, and safe preemption.
    - Upgradeable to 64 GB profiles without organizational rewrites.
    """

    def __init__(
        self,
        profiler: Optional[HardwareProfiler] = None,
        monitor: Optional[ResourceMonitor] = None,
        slot_pool: Optional[DesktopSlotPool] = None,
        lease_manager: Optional[SessionLeaseManager] = None,
        checkpoint_manager: Optional[CheckpointManager] = None,
        queue: Optional[EmployeeExecutionQueue] = None,
        resource_lease_repo: Optional[ResourceLeaseRepository] = None,
        resource_lease_seconds: int = 1800,
        resource_pool_id: Optional[str] = None,
    ) -> None:
        self.profiler = profiler or HardwareProfiler()
        self.monitor = monitor or ResourceMonitor()
        self.slot_pool = slot_pool or DesktopSlotPool(max_slots=self.profiler.get_max_desktops())
        self.lease_manager = lease_manager or SessionLeaseManager()
        self.checkpoint_manager = checkpoint_manager or CheckpointManager()
        self.queue = queue or EmployeeExecutionQueue()
        self.resource_lease_repo = resource_lease_repo
        self.resource_lease_seconds = max(5, min(resource_lease_seconds, 3600))
        self.resource_pool_id = (
            resource_pool_id or os.environ.get("ANCOS_RESOURCE_POOL_ID") or socket.gethostname()
        ).strip()
        if not self.resource_pool_id or len(self.resource_pool_id) > 96:
            raise ValueError("Resource pool ID must contain between 1 and 96 characters.")
        self._resource_leases: dict[int, dict[str, Any]] = {}

        self._active_employees: dict[str, int] = {}  # employee_id -> slot_id
        self._standby_employees: set[str] = set()

    def set_hardware_profile(self, profile: HardwareProfile | str) -> None:
        """Dynamically scales capacity (e.g. from 16 GB to 32 GB or 64 GB)."""
        self.profiler.set_profile(profile)
        max_slots = self.profiler.get_max_desktops()
        self.slot_pool.set_max_slots(max_slots)
        logger.info("Scheduler adapted to hardware profile", profile=self.profiler.active_profile.profile_name, max_slots=max_slots)

    async def request_desktop_session(
        self,
        employee_id: str,
        task_id: str,
        department: str = "Engineering",
        priority: TaskPriority = TaskPriority.MEDIUM,
        role_image: str = "employee-base",
    ) -> dict[str, Any]:
        """
        Requests an active GUI desktop slot for an employee.
        If a slot is available and host is healthy -> Allocates slot & container.
        If all configured desktop slots are occupied -> Puts employee into STANDBY.
        """
        # 0. Reconcile any expired session leases first to free up slots
        await self.reconcile_expired_leases()

        # 1. If employee already has active slot, renew lease and return
        if employee_id in self._active_employees:
            slot_id = self._active_employees[employee_id]
            self.lease_manager.heartbeat(slot_id)
            durable_lease = await self._heartbeat_resource_lease(slot_id)
            slot = self.slot_pool._slots[slot_id]
            result = {
                "status": "active",
                "slot_id": slot_id,
                "desktop_url": slot.desktop_url,
                "vnc_port": slot.vnc_port,
                "is_new_allocation": False,
            }
            if durable_lease:
                result["resource_lease"] = durable_lease
            return result

        # 2. Check host health via ResourceMonitor
        safe, reason = self.monitor.is_safe_to_admit(self.profiler.active_profile)
        if not safe:
            logger.warning("Host resource safety threshold exceeded; enqueuing employee as standby", reason=reason)
            self._standby_employees.add(employee_id)
            self.queue.enqueue(
                employee_id=employee_id,
                task_id=task_id,
                department=department,
                priority=priority,
                metadata={"reason": reason},
            )
            return {
                "status": "queued_standby",
                "employee_id": employee_id,
                "reason": reason,
                "queue_position": self.queue.size(),
            }

        # 3. Check physical slot capacity
        max_slots = self.profiler.get_max_desktops()
        active_count = self.slot_pool.get_active_count()

        if max_slots == 0:
            reason = "Desktop capacity is disabled by deployment configuration."
            self._standby_employees.add(employee_id)
            self.queue.enqueue(
                employee_id=employee_id,
                task_id=task_id,
                department=department,
                priority=priority,
                metadata={"reason": reason},
            )
            return {
                "status": "queued_standby",
                "employee_id": employee_id,
                "reason": reason,
                "queue_position": self.queue.size(),
            }

        if active_count >= max_slots:
            # Capacity full! Standby employee concept
            logger.info(
                "Max active desktop capacity reached; queuing employee in standby",
                active_count=active_count,
                max_slots=max_slots,
                employee_id=employee_id,
            )
            self._standby_employees.add(employee_id)
            self.queue.enqueue(
                employee_id=employee_id,
                task_id=task_id,
                department=department,
                priority=priority,
            )
            return {
                "status": "queued_standby",
                "employee_id": employee_id,
                "reason": f"Active desktop capacity full ({active_count}/{max_slots} slots)",
                "queue_position": self.queue.size(),
            }

        # 4. Free slot exists: Allocate desktop slot
        free_slots = [
            slot for slot in self.slot_pool._slots.values()
            if slot.slot_id <= self.slot_pool.max_slots and slot.is_free
        ]
        if not free_slots:
            # Fallback queue
            self._standby_employees.add(employee_id)
            self.queue.enqueue(employee_id=employee_id, task_id=task_id, department=department, priority=priority)
            return {"status": "queued_standby", "employee_id": employee_id, "queue_position": self.queue.size()}

        allocated_slot = None
        durable_lease = None
        for free_slot in free_slots:
            durable_lease = await self._claim_resource_lease(free_slot.slot_id, employee_id, task_id)
            if self.resource_lease_repo and durable_lease is None:
                continue
            if durable_lease:
                self._resource_leases[free_slot.slot_id] = durable_lease
            try:
                allocated_slot = await self.slot_pool.provision_slot(
                    slot_id=free_slot.slot_id,
                    employee_id=employee_id,
                    role_image=role_image,
                    cpu_limit=self.profiler.active_profile.container_cpu_limit,
                    mem_limit=self.profiler.active_profile.container_mem_limit,
                )
            except Exception:
                if durable_lease:
                    await self._release_resource_lease(free_slot.slot_id)
                raise
            break

        if allocated_slot is None:
            reason = "All locally free desktop slots have active durable resource leases."
            self._standby_employees.add(employee_id)
            self.queue.enqueue(
                employee_id=employee_id,
                task_id=task_id,
                department=department,
                priority=priority,
                metadata={"reason": reason},
            )
            return {
                "status": "queued_standby",
                "employee_id": employee_id,
                "reason": reason,
                "queue_position": self.queue.size(),
            }

        self.lease_manager.grant(slot_id=allocated_slot.slot_id, employee_id=employee_id)
        self._active_employees[employee_id] = allocated_slot.slot_id
        self._standby_employees.discard(employee_id)

        # Check if resuming from saved checkpoint
        checkpoint = self.checkpoint_manager.load_checkpoint(employee_id, task_id)

        result = {
            "status": "active",
            "employee_id": employee_id,
            "slot_id": allocated_slot.slot_id,
            "desktop_url": allocated_slot.desktop_url,
            "vnc_port": allocated_slot.vnc_port,
            "novnc_port": allocated_slot.novnc_port,
            "is_new_allocation": True,
            "resumed_from_checkpoint": checkpoint is not None,
            "checkpoint_step": checkpoint.get("step_index") if checkpoint else None,
        }
        if durable_lease:
            result["resource_lease"] = durable_lease
        return result

    async def suspend_and_release(
        self,
        employee_id: str,
        task_id: str,
        current_step: int = 0,
        state_dict: Optional[dict[str, Any]] = None,
        pending_operations: Optional[list[dict[str, Any]]] = None,
        dispatch_next: bool = True,
    ) -> dict[str, Any]:
        """
        Safely suspends an active employee:
        1. Persists execution state to durable disk outside container.
        2. Terminates desktop container and releases ports.
        3. Moves employee to STANDBY.
        4. Grants slot to next queued standby employee if available.
        """
        if employee_id not in self._active_employees:
            return {"status": "not_active", "employee_id": employee_id}

        slot_id = self._active_employees[employee_id]

        # 1. Save durable checkpoint
        slot = self.slot_pool._slots[slot_id]
        checkpoint_path = self.checkpoint_manager.save_checkpoint(
            employee_id=employee_id,
            task_id=task_id,
            step_index=current_step,
            state_dict=state_dict or {},
            pending_operations=pending_operations or [],
            workspace_path=slot.workspace_path,
        )

        # 2. Release slot and stop container
        if not await self.slot_pool.release_slot(slot_id):
            raise RuntimeError(f"Desktop slot {slot_id} could not be safely released.")
        self._active_employees.pop(employee_id, None)
        self.lease_manager.revoke(slot_id)
        await self._release_resource_lease(slot_id)
        self._standby_employees.add(employee_id)
        logger.info("Employee safely suspended to standby with checkpoint", employee_id=employee_id, task_id=task_id)

        # 3. Serve next queued employee if requested
        next_allocation = None
        if dispatch_next:
            next_allocation = await self._dispatch_next_queued_employee()

        return {
            "status": "suspended_to_standby",
            "employee_id": employee_id,
            "checkpoint_saved": True,
            "checkpoint_path": checkpoint_path,
            "released_slot_id": slot_id,
            "dispatched_next": next_allocation,
        }

    async def _dispatch_next_queued_employee(self) -> Optional[dict[str, Any]]:
        if self.queue.is_empty():
            return None

        free_slot = self.slot_pool.get_free_slot()
        if not free_slot:
            return None

        queued = self.queue.dequeue()
        if not queued:
            return None

        res = await self.request_desktop_session(
            employee_id=queued.employee_id,
            task_id=queued.task_id,
            department=queued.department,
            priority=queued.priority,
        )
        return res

    async def reconcile_expired_leases(self) -> list[int]:
        """
        Reconciles and reclaims all desktop slots whose session leases have expired.
        Saves durable checkpoints and releases slots so queued standby employees can proceed.
        """
        expired_slots = set(self.lease_manager.find_expired_slots())
        for slot_id in list(self._resource_leases):
            if slot_id in expired_slots:
                continue
            if await self._heartbeat_resource_lease(slot_id) is None:
                expired_slots.add(slot_id)
        reclaimed: list[int] = []
        for slot_id in sorted(expired_slots):
            slot = self.slot_pool._slots.get(slot_id)
            emp_id = slot.employee_id if slot else None
            if not emp_id:
                for e_id, s_id in list(self._active_employees.items()):
                    if s_id == slot_id:
                        emp_id = e_id
                        break
            if emp_id:
                logger.warning(
                    "Session lease expired; safely suspending employee to standby",
                    slot_id=slot_id,
                    employee_id=emp_id,
                )
                await self.suspend_and_release(
                    employee_id=emp_id,
                    task_id="lease_expired",
                    current_step=0,
                    state_dict={"reason": "session_lease_expired"},
                    dispatch_next=False,
                )
                reclaimed.append(slot_id)
            else:
                self.lease_manager.revoke(slot_id)
                await self.slot_pool.release_slot(slot_id)
                reclaimed.append(slot_id)

        # If any slots were reclaimed, trigger dispatch of next queued standby employees
        if reclaimed:
            await self.dispatch_queued_employees()

        return reclaimed

    async def dispatch_queued_employees(self) -> list[dict[str, Any]]:
        """Dispatches queued standby employees into all currently available desktop slots."""
        dispatched: list[dict[str, Any]] = []
        while not self.queue.is_empty() and self.slot_pool.get_free_slot():
            res = await self._dispatch_next_queued_employee()
            if not res or res.get("status") != "active":
                break
            dispatched.append(res)
        return dispatched

    async def emergency_capacity_shutdown(self) -> int:
        """Emergency safety circuit: safely suspends all active employees if host exhausted."""
        logger.warning("Initiating Emergency Capacity Shutdown")
        count = 0
        for emp_id in list(self._active_employees.keys()):
            await self.suspend_and_release(
                employee_id=emp_id,
                task_id="emergency_halt",
                current_step=0,
                state_dict={"reason": "emergency_capacity_shutdown"},
                dispatch_next=False,
            )
            count += 1
        return count

    def get_stats(self) -> dict[str, Any]:
        return {
            "profile": self.profiler.active_profile.to_dict(),
            "max_slots": self.profiler.get_max_desktops(),
            "active_desktops_count": self.slot_pool.get_active_count(),
            "standby_employees_count": len(self._standby_employees),
            "queue_depth": self.queue.size(),
            "active_employees": list(self._active_employees.keys()),
            "standby_employees": list(self._standby_employees),
            "queued_employees": self.queue.list_queue(),
            "slots": self.slot_pool.list_slots(),
        }

    async def _claim_resource_lease(
        self, slot_id: int, employee_id: str, task_id: str
    ) -> Optional[dict[str, Any]]:
        if not self.resource_lease_repo:
            return None
        profile = self.profiler.active_profile
        return await self.resource_lease_repo.claim(
            resource_key=f"desktop:{self.resource_pool_id}:slot-{slot_id}",
            employee_id=employee_id,
            task_id=task_id,
            session_id=f"desktop-slot-{slot_id}:{employee_id}",
            lease_seconds=self.resource_lease_seconds,
            resource_reservation={
                "slot_id": slot_id,
                "resource_pool_id": self.resource_pool_id,
                "profile": profile.profile_name,
                "cpu_limit": profile.container_cpu_limit,
                "mem_limit": profile.container_mem_limit,
            },
        )

    async def _heartbeat_resource_lease(self, slot_id: int) -> Optional[dict[str, Any]]:
        lease = self._resource_leases.get(slot_id)
        if not lease or not self.resource_lease_repo:
            return None
        expiry = await self.resource_lease_repo.heartbeat(
            lease_id=lease["lease_id"],
            fencing_token=lease["fencing_token"],
            lease_seconds=self.resource_lease_seconds,
        )
        if expiry is None:
            self._resource_leases.pop(slot_id, None)
            return None
        lease["lease_expires_at"] = expiry
        return dict(lease)

    async def _release_resource_lease(self, slot_id: int) -> bool:
        lease = self._resource_leases.pop(slot_id, None)
        if not lease or not self.resource_lease_repo:
            return True
        return await self.resource_lease_repo.release(
            lease_id=lease["lease_id"],
            fencing_token=lease["fencing_token"],
        )

