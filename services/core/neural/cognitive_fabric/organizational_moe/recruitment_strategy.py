from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.moe.recruitment")


class RecruitmentStrategy:
    """
    On-Demand Specialist Recruitment.
    When task requirements cannot be satisfied by the current workforce,
    dynamically defines and provisions a new AI specialist role.
    """

    @staticmethod
    def needs_recruitment(
        candidate_evaluations: Sequence[dict[str, Any]],
        required_capabilities: Sequence[str],
        min_fit_threshold: float = 15.0,
    ) -> bool:
        if not required_capabilities:
            return False
        if not candidate_evaluations:
            return True

        # Check if the top candidate has at least one matching skill or exceeds min threshold
        top_fit = max(candidate_evaluations, key=lambda c: c["composite_score"])
        has_match = (top_fit["verified_matches"] > 0 or top_fit["unverified_matches"] > 0)
        if not has_match and top_fit["composite_score"] < min_fit_threshold:
            return True
        return False

    @staticmethod
    def design_recruitment_profile(
        required_capabilities: Sequence[str],
        department: str = "Engineering",
    ) -> dict[str, Any]:
        caps = [c.strip() for c in required_capabilities if c.strip()]
        role_label = caps[0].title() if caps else "Specialist"
        short_id = uuid.uuid4().hex[:4].upper()

        name = f"Specialist-{role_label}-{short_id}"
        title = f"{role_label} Specialist Engineer"
        personality = f"Deeply specialized in {', '.join(caps)}; rigorously grounded in verified execution."

        approved_tools = ["terminal", "code_editor", "file_ops", "git"]
        if any(c.lower() in ("gui", "browser", "frontend", "visual") for c in caps):
            approved_tools.append("browser")

        return {
            "name": name,
            "title": title,
            "department": department,
            "required_skills": caps,
            "personality": personality,
            "approved_tools": approved_tools,
            "system_prompt": (
                f"You are {name}, a {title} at Autonomous Neural Company (ANCOS).\n"
                f"Your core competencies include: {', '.join(caps)}.\n"
                "Verify every deliverable empirically with pass receipts before claiming completion."
            ),
        }

    async def recruit_specialist(
        self,
        required_capabilities: Sequence[str],
        employee_factory: Any,
        department: str = "Engineering",
    ) -> Optional[Any]:
        profile = self.design_recruitment_profile(required_capabilities, department)
        logger.info("Recruiting on-demand specialist for gap", capabilities=required_capabilities, name=profile["name"])
        try:
            employee = await employee_factory.hire_employee(
                name=profile["name"],
                title=profile["title"],
                department=profile["department"],
                personality=profile["personality"],
                initial_skills=profile["required_skills"],
            )
            return employee
        except Exception as e:
            logger.error("Failed to recruit specialist", error=str(e))
            return None

