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

        # Department clearance check
        if record.allowed_departments:
            if not department or department not in record.allowed_departments:
                return False

        # Project clearance check
        if record.allowed_projects:
            active_projects = set(projects or [])
            if not active_projects.intersection(set(record.allowed_projects)):
                return False

        # Confidential records require explicit department or project assignment
        if record.access_level == MemoryAccessLevel.CONFIDENTIAL:
            has_dept = bool(department and record.allowed_departments and department in record.allowed_departments)
            has_proj = bool(projects and record.allowed_projects and set(projects).intersection(set(record.allowed_projects)))
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

