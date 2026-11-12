from typing import Any, Optional

from neural.logger import get_logger
from neural.workstations.layout import CANONICAL_WORKSTATIONS, VALID_DESK_IDS

logger = get_logger("ancos.workstations.assignment")


def infer_desk_role(department: str = "", title: str = "") -> str:
    """Infers canonical desk role from department and title strings."""
    d = (department or "").lower()
    t = (title or "").lower()

    # Refine "chief" check so "Chief Architect" or "Chief Scientist" does not map to "boss"
    is_non_boss_chief = "chief" in t and any(nb in t for nb in ("architect", "scientist", "research"))
    if not is_non_boss_chief and any(k in d or k in t for k in ("exec", "ceo", "boss", "chief")):
        return "boss"
    if any(k in d or k in t for k in ("qa", "quality", "test", "verification")):
        return "qa"
    if any(k in d or k in t for k in ("infra", "devops", "cloud", "ops", "operation", "platform", "sre")):
        return "devops"
    if any(k in d or k in t for k in ("research", "ml", "ai", "scientist", "neural")):
        return "research"
    if any(k in d or k in t for k in ("creative", "design", "ux", "art", "product")):
        return "creative"
    if "frontend" in d or "frontend" in t or "ui" in d or "ui" in t:
        return "frontend"
    return "swe"


class WorkstationAssignmentService:
    """
    Manages persistent office desk assignments for ANCOS employees.
    Grounds visual dashboard workstations in transactional database state.
    """

    @classmethod
    def list_canonical_workstations(cls) -> list[dict[str, Any]]:
        return [dict(w) for w in CANONICAL_WORKSTATIONS]

    @classmethod
    def is_valid_desk_id(cls, desk_id: str) -> bool:
        if not desk_id or not isinstance(desk_id, str):
            return False
        return desk_id in VALID_DESK_IDS or desk_id.startswith("WS-OVERFLOW-")

    @classmethod
    def allocate_desk(
        cls,
        department: str,
        title: Any = None,
        occupied_desks: Optional[set[str]] = None,
        employee_id: Optional[str] = None,
    ) -> str:
        """
        Allocates the most suitable vacant workstation desk based on role/department.
        Falls back to any vacant canonical desk, and finally deterministic overflow desk.
        Supports both (role, occupied_desks) and (dept, title, occupied_desks) call styles.
        """
        if isinstance(title, (set, list, tuple)) and occupied_desks is None:
            occupied = set(title)
            if department in {"boss", "swe", "frontend", "qa", "devops", "research", "creative"}:
                role = "swe" if department == "frontend" else department
                dept_name = ""
            else:
                role = infer_desk_role(department, "")
                dept_name = department
        else:
            occupied = set(occupied_desks or set())
            role = infer_desk_role(department, str(title or ""))
            dept_name = department

        # 1. Preferred desks matching role
        matching = [w for w in CANONICAL_WORKSTATIONS if w["role"] == role and w["id"] not in occupied]
        if matching:
            chosen = matching[0]["id"]
            logger.info("Allocated preferred workstation desk", desk_id=chosen, role=role, employee_id=employee_id)
            return chosen

        # 2. Preferred desks matching department
        dept_lower = dept_name.lower()
        if dept_lower:
            dept_matching = [
                w for w in CANONICAL_WORKSTATIONS
                if w["department"].lower() in dept_lower and w["id"] != "WS-BOSS" and w["id"] not in occupied
            ]
            if dept_matching:
                chosen = dept_matching[0]["id"]
                logger.info("Allocated department workstation desk", desk_id=chosen, department=department, employee_id=employee_id)
                return chosen

        # 3. Any vacant non-executive canonical desk
        general_vacant = [
            w for w in CANONICAL_WORKSTATIONS
            if w["id"] != "WS-BOSS" and w["id"] not in occupied
        ]
        if general_vacant:
            chosen = general_vacant[0]["id"]
            logger.info("Allocated fallback canonical workstation desk", desk_id=chosen, employee_id=employee_id)
            return chosen

        # 4. Overflow desk if all canonical workstations are occupied
        overflow_index = 1
        while f"WS-OVERFLOW-{overflow_index:02d}" in occupied:
            overflow_index += 1
        overflow_id = f"WS-OVERFLOW-{overflow_index:02d}"
        logger.info("Allocated overflow workstation desk", desk_id=overflow_id, employee_id=employee_id)
        return overflow_id
