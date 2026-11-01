from __future__ import annotations

from typing import Any, Optional, Sequence

from neural.db.models import Employee, Task
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.moe.capability_matcher")


class CapabilityMatcher:
    """
    Evaluates employee candidate competence for tasks.
    Enforces the core mandate: Empirically verified skills are prioritized
    over unverified claims. Integrates historical quality and retry records.
    """

    @staticmethod
    def evaluate_employee_fit(
        employee: Employee,
        required_capabilities: Sequence[str],
        task: Optional[Task] = None,
    ) -> dict[str, Any]:
        req_set = {c.strip().lower() for c in required_capabilities if c.strip()}
        perf_summary = employee.performance_summary or {}

        # 1. Skill analysis
        verified_skills: set[str] = set()
        unverified_skills: set[str] = set()

        for s in employee.skills:
            s_name = s.skill_name.strip().lower()
            if getattr(s, "verified", False):
                verified_skills.add(s_name)
            else:
                unverified_skills.add(s_name)

        verified_matches = len(req_set.intersection(verified_skills))
        unverified_matches = len(req_set.intersection(unverified_skills))
        missing_skills = list(req_set - verified_skills - unverified_skills)

        # 2. Historical performance & quality metrics
        quality_score = float(perf_summary.get("quality_score", 1.0))
        success_rate = float(perf_summary.get("success_rate", 1.0))
        historical_tasks = int(perf_summary.get("completed_tasks", 0))

        # 3. Composite Suitability Score
        # Verified skills: 35 pts each + 20 pt verification bonus.
        # Unverified claims: capped at 12 pts max so they can NEVER outscore a verified skill.
        verified_score = (verified_matches * 35.0) + (20.0 if verified_matches > 0 else 0.0)
        unverified_score = min(12.0, unverified_matches * 3.0)
        skill_score = verified_score + unverified_score

        # Quality multiplier
        quality_multiplier = max(0.2, min(1.5, quality_score * success_rate))
        composite_score = (skill_score + 10.0) * quality_multiplier

        # If task has explicit requirements and candidate has 0 matching skills, score is heavily reduced
        if req_set and verified_matches == 0 and unverified_matches == 0:
            composite_score = max(0.5, composite_score * 0.1)

        return {
            "employee_id": employee.employee_id,
            "name": employee.name,
            "title": employee.title,
            "department": employee.department,
            "composite_score": round(composite_score, 2),
            "verified_matches": verified_matches,
            "unverified_matches": unverified_matches,
            "missing_skills": missing_skills,
            "quality_score": quality_score,
            "historical_tasks": historical_tasks,
            "has_verified_match": verified_matches > 0,
        }

