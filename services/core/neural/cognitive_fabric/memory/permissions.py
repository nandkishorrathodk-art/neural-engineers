from __future__ import annotations

from typing import Iterable, Optional, Sequence

from neural.cognitive_fabric.memory.models import MemoryAccessLevel, MemoryRecord
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.memory.permissions")


class MemoryAccessController:
    """
    Enforces organizational RBAC and privacy isolation on memory records.
    Separates private employee memory from shared company memory, and prevents
    unauthorized cross-project or cross-department knowledge leakage.
    """

    @staticmethod
    def can_read(
        record: MemoryRecord,
        requester_id: str,
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
    ) -> bool:
        # Boss / Executive has complete organizational oversight
        clean_req = requester_id.strip().lower()
        if clean_req in ("boss", "bossai", "admin", "system"):
            return True

        # Public records readable by all authenticated employees
        if record.access_level == MemoryAccessLevel.PUBLIC:
            return True

        # Private Employee Memory: strictly isolated to the owner
        if record.owner_employee_id:
            if record.owner_employee_id == requester_id:
                return True
            if record.access_level == MemoryAccessLevel.RESTRICTED:
                # Disallow any other employee
                return False

        # Any restricted record where requester is not the owner or an executive must be blocked
        if record.access_level == MemoryAccessLevel.RESTRICTED:
            return False

        # Department clearance check
        if record.allowed_departments:
            dept_set = {d.strip().casefold() for d in record.allowed_departments if d}
            req_dept = department.strip().casefold() if department else None
            if not req_dept or req_dept not in dept_set:
                return False

        # Project clearance check
        if record.allowed_projects:
            proj_set = {p.strip().casefold() for p in record.allowed_projects if p}
            active_projects = {p.strip().casefold() for p in (projects or []) if p}
            if not active_projects.intersection(proj_set):
                return False

        # Confidential records require explicit department or project assignment
        if record.access_level == MemoryAccessLevel.CONFIDENTIAL:
            dept_set = {d.strip().casefold() for d in record.allowed_departments if d}
            req_dept = department.strip().casefold() if department else None
            has_dept = bool(req_dept and dept_set and req_dept in dept_set)
            proj_set = {p.strip().casefold() for p in record.allowed_projects if p}
            active_projects = {p.strip().casefold() for p in (projects or []) if p}
            has_proj = bool(active_projects and proj_set and active_projects.intersection(proj_set))
            if not (has_dept or has_proj or (record.owner_employee_id == requester_id)):
                return False

        return True

    @classmethod
    def filter_records(
        cls,
        records: Iterable[MemoryRecord],
        requester_id: str,
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
    ) -> list[MemoryRecord]:
        accessible = [
            r for r in records
            if cls.can_read(r, requester_id, department=department, projects=projects)
        ]
        return accessible

