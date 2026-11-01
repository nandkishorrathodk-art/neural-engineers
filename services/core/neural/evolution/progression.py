from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from neural.constants import SKILL_LEVEL_RANK, SkillLevel
from neural.db.models import Employee
from neural.db.repositories import EmployeeRepository
from neural.logger import get_logger

logger = get_logger("ancos.evolution.progression")


class SkillProgressionTracker:
    """
    Tracks and manages employee technical progression across standard tiers:
    NOVICE -> COMPETENT -> EXPERT -> MASTER.
    Enforces empirical upgrades based on passing verified benchmark tests in isolated sandboxes.
    Generates company-wide skill matrices.
    """

    PROGRESSION_ORDER: list[SkillLevel] = [
        SkillLevel.NOVICE,
        SkillLevel.COMPETENT,
        SkillLevel.EXPERT,
        SkillLevel.MASTER,
    ]

    def __init__(self, employee_repo: EmployeeRepository | None = None):
        self.employee_repo = employee_repo

    @classmethod
    def get_tier_rank(cls, level: SkillLevel | str) -> int:
        lvl_str = level.value.lower() if hasattr(level, "value") else str(level).lower()
        return SKILL_LEVEL_RANK.get(lvl_str, 1)

    @classmethod
    def get_next_tier(cls, current_level: SkillLevel | str) -> SkillLevel | None:
        curr_rank = cls.get_tier_rank(current_level)
        for lvl in cls.PROGRESSION_ORDER:
            if cls.get_tier_rank(lvl) == curr_rank + 1:
                return lvl
        return None

    @classmethod
    def can_upgrade(
        cls,
        current_level: SkillLevel | str,
        candidate_level: SkillLevel | str
    ) -> bool:
        curr_rank = cls.get_tier_rank(current_level)
        cand_rank = cls.get_tier_rank(candidate_level)
        return cand_rank > curr_rank

    async def upgrade_employee_skill(
        self,
        employee_id: str,
        skill_name: str,
        target_level: SkillLevel | str,
        evidence_id: str,
    ) -> dict[str, Any]:
        """
        Empirically upgrades an employee's skill level upon verified proof.
        """
        if not self.employee_repo:
            raise ValueError("EmployeeRepository is required to record progression")

        lvl_enum = target_level if isinstance(target_level, SkillLevel) else SkillLevel(target_level.lower())
        emp = await self.employee_repo.get_by_employee_id(employee_id)
        if not emp:
            raise ValueError(f"Employee {employee_id} not found")

        # Check existing level
        existing_skill = next((s for s in emp.skills if s.skill_name.lower() == skill_name.lower()), None)
        was_verified = existing_skill.verified if existing_skill else False
        old_level_str = (
            (existing_skill.level.value if hasattr(existing_skill.level, "value") else str(existing_skill.level))
            if existing_skill
            else "unrated"
        )

        upgraded_skill = await self.employee_repo.upgrade_skill_level(
            employee_id=employee_id,
            skill_name=skill_name,
            new_level=lvl_enum,
            task_evidence_id=evidence_id,
        )

        new_level_str = upgraded_skill.level.value if hasattr(upgraded_skill.level, "value") else str(upgraded_skill.level)

        logger.info(
            "Empirical skill progression recorded",
            employee_id=employee_id,
            skill=skill_name,
            old_level=old_level_str,
            new_level=new_level_str,
            evidence=evidence_id,
        )

        return {
            "employee_id": employee_id,
            "skill_name": skill_name,
            "previous_level": old_level_str,
            "new_level": new_level_str,
            "verified": upgraded_skill.verified,
            "evidence_id": evidence_id,
            "upgraded": (old_level_str != new_level_str) or (not was_verified and upgraded_skill.verified),
        }

    def generate_matrix(self, employees: Sequence[Employee]) -> dict[str, Any]:
        """
        Generates structured workforce competency matrix:
        Employees x Skills with levels and verified badges.
        """
        all_skills: set[str] = set()
        employee_rows = []

        for emp in employees:
            skills_dict = {}
            for s in emp.skills:
                all_skills.add(s.skill_name)
                lvl = s.level.value if hasattr(s.level, "value") else str(s.level)
                skills_dict[s.skill_name] = {
                    "level": lvl,
                    "rank": self.get_tier_rank(s.level),
                    "verified": s.verified,
                    "verified_at": s.verified_at.isoformat() if s.verified_at else None,
                    "evidence_count": len(s.evidence_task_ids or []),
                }

            employee_rows.append({
                "employee_id": emp.employee_id,
                "name": emp.name,
                "title": emp.title,
                "department": emp.department,
                "status": emp.status.value if hasattr(emp.status, "value") else str(emp.status),
                "skills": skills_dict,
                "verified_count": len([s for s in emp.skills if s.verified]),
                "total_skills": len(emp.skills),
            })

        sorted_skills = sorted(list(all_skills))
        total_employees = len(employees)
        verified_coverage = {}
        for skill in sorted_skills:
            verified_count = sum(1 for e in employees if any(s.skill_name.lower() == skill.lower() and s.verified for s in e.skills))
            verified_coverage[skill] = {
                "verified_employees": verified_count,
                "coverage_pct": round((verified_count / total_employees) * 100, 1) if total_employees > 0 else 0.0,
            }

        return {
            "total_employees": total_employees,
            "skills_catalog": sorted_skills,
            "matrix": employee_rows,
            "coverage": verified_coverage,
        }
