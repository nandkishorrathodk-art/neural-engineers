from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from neural.containers.manager import ContainerManager
from neural.logger import get_logger

logger = get_logger("ancos.resource_scheduler.desktop_pool")


@dataclass
class DesktopSlot:
    slot_id: int
    vnc_port: int
    novnc_port: int
    employee_id: Optional[str] = None
    container_id: Optional[str] = None
    workspace_path: Optional[str] = None
    desktop_url: Optional[str] = None
    allocated_at: Optional[float] = None
    status: str = "free"  # free, active, suspended

    @property
    def is_free(self) -> bool:
        return self.status == "free" and self.employee_id is None

    def allocate(
        self,
        employee_id: str,
        container_id: str,
        workspace_path: str,
        desktop_url: str,
    ) -> None:
        self.employee_id = employee_id
        self.container_id = container_id
        self.workspace_path = workspace_path
        self.desktop_url = desktop_url
        self.allocated_at = time.time()
        self.status = "active"

    def release(self) -> None:
        self.employee_id = None
        self.container_id = None
        self.workspace_path = None
        self.desktop_url = None
        self.allocated_at = None
        self.status = "free"

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "vnc_port": self.vnc_port,
            "novnc_port": self.novnc_port,
            "employee_id": self.employee_id,
            "container_id": self.container_id,
            "desktop_url": self.desktop_url,
            "status": self.status,
            "allocated_at": self.allocated_at,
            "is_free": self.is_free,
        }


class DesktopSlotPool:
    """
    Manages dedicated physical slots for concurrent GUI desktop sessions.
    Slot capacity follows the hardware profile/deployment ceiling supplied by the scheduler.
    """

    def __init__(
        self,
        max_slots: int = 4,
        container_manager: Optional[ContainerManager] = None,
        base_vnc_port: int = 5901,
        base_novnc_port: int = 6081,
    ) -> None:
        self.max_slots = 0
        self.container_manager = container_manager or ContainerManager()
        self.base_vnc_port = base_vnc_port
        self.base_novnc_port = base_novnc_port
        self._slots: dict[int, DesktopSlot] = {}
        self._reinitialize_slots(max_slots)

    def _reinitialize_slots(self, count: int) -> None:
        if count < 0:
            raise ValueError("Desktop slot capacity cannot be negative.")
        self.max_slots = count
        # Purge any free slots beyond count when downscaling
        for i in list(self._slots.keys()):
            if i > count and self._slots[i].is_free:
                del self._slots[i]
        # Keep existing active slots, initialize up to count
        for i in range(1, count + 1):
            if i not in self._slots:
                vnc = self.base_vnc_port + (i - 1)
                novnc = self.base_novnc_port + (i - 1)
                self._slots[i] = DesktopSlot(slot_id=i, vnc_port=vnc, novnc_port=novnc)

    def set_max_slots(self, new_max: int) -> None:
        self._reinitialize_slots(new_max)

    def get_free_slot(self) -> Optional[DesktopSlot]:
        for slot in self._slots.values():
            if slot.slot_id <= self.max_slots and slot.is_free:
                return slot
        return None

    def get_slot_by_employee(self, employee_id: str) -> Optional[DesktopSlot]:
        for slot in self._slots.values():
            if slot.slot_id <= self.max_slots and slot.employee_id == employee_id:
                return slot
        return None

    def get_active_count(self) -> int:
        return sum(1 for s in self._slots.values() if s.slot_id <= self.max_slots and not s.is_free)

    async def provision_slot(
        self,
        slot_id: int,
        employee_id: str,
        role_image: str = "employee-base",
        cpu_limit: Optional[float] = None,
        mem_limit: Optional[str] = None,
    ) -> DesktopSlot:
        slot = self._slots.get(slot_id)
        if not slot:
            raise ValueError(f"Invalid slot ID {slot_id}")
        if not slot.is_free:
            raise RuntimeError(f"Slot {slot_id} is already occupied by {slot.employee_id}")

        logger.info("Allocating desktop slot to employee", slot_id=slot_id, employee_id=employee_id)

        # Provision container via ContainerManager
        res = await self.container_manager.provision_employee_desktop(
            employee_id=employee_id,
            role_image=role_image,
            cpu_limit=cpu_limit,
            mem_limit=mem_limit,
        )

        docker_id = res.get("docker_id", f"ancos-desktop-{employee_id.lower()}")
        ws_path = res.get("workspace", f"./data/workspaces/{employee_id}")
        url = res.get("desktop_url", f"http://localhost:{slot.novnc_port}/vnc.html?autoconnect=true")

        slot.allocate(
            employee_id=employee_id,
            container_id=docker_id,
            workspace_path=ws_path,
            desktop_url=url,
        )
        return slot

    async def release_slot(self, slot_id: int) -> bool:
        slot = self._slots.get(slot_id)
        if not slot or slot.is_free:
            return False

        emp_id = slot.employee_id
        logger.info("Releasing desktop slot", slot_id=slot_id, employee_id=emp_id)

        # Terminate container via ContainerManager if applicable
        if emp_id:
            try:
                docker_id = slot.container_id or f"ancos-desktop-{emp_id.lower()}"
                if hasattr(self.container_manager, "terminate_desktop"):
                    terminated = await self.container_manager.terminate_desktop(docker_id)
                elif hasattr(self.container_manager, "terminate_employee_desktop"):
                    terminated = await self.container_manager.terminate_employee_desktop(emp_id)
                else:
                    logger.error("Container manager has no workstation termination method", slot_id=slot_id)
                    return False
                if terminated is False:
                    logger.error("Workstation termination was not confirmed; retaining occupied slot", slot_id=slot_id)
                    return False
            except Exception as e:
                logger.warning("Error terminating desktop container during slot release", error=str(e))
                return False

        slot.release()
        return True

    def list_slots(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._slots.values() if s.slot_id <= self.max_slots]

