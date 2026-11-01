from __future__ import annotations

from typing import Optional

from neural.gui_intelligence.skills.skill_compiler import CompiledGUISkill
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.skills.skill_registry")


class GUISkillRegistry:
    """
    Verified Procedural GUI Skill Registry (Section 7.1 & Section 18).
    Maintains an in-memory & persistent catalog of certified procedural GUI skills.
    Provides retrieval by application name, skill name, or target postcondition.
    """

    def __init__(self) -> None:
        self._skills: dict[str, CompiledGUISkill] = {}
        self._certified_skill_ids: set[str] = set()

    def register_skill(self, skill: CompiledGUISkill, is_certified: bool = False) -> None:
        """Registers a compiled GUI skill in the catalog."""
        self._skills[skill.skill_id] = skill
        if is_certified:
            self._certified_skill_ids.add(skill.skill_id)
        logger.info(
            "Registered GUI skill",
            skill_id=skill.skill_id,
            skill_name=skill.skill_name,
            is_certified=is_certified,
        )

    def mark_certified(self, skill_id: str) -> bool:
        """Marks a skill as empirically certified following held-out validation."""
        if skill_id in self._skills:
            self._certified_skill_ids.add(skill_id)
            return True
        return False

    def is_certified(self, skill_id: str) -> bool:
        """Checks if a skill has passed empirical certification."""
        return skill_id in self._certified_skill_ids

    def get_skill(self, skill_id: str) -> Optional[CompiledGUISkill]:
        """Retrieves a skill by ID."""
        return self._skills.get(skill_id)

    def list_skills(
        self,
        application_name: Optional[str] = None,
        only_certified: bool = False,
    ) -> list[CompiledGUISkill]:
        """Lists skills with optional filtering by application and certification status."""
        skills = list(self._skills.values())
        if application_name:
            norm_app = application_name.strip().lower()
            skills = [s for s in skills if s.application_name.lower() == norm_app]
        if only_certified:
            skills = [s for s in skills if s.skill_id in self._certified_skill_ids]
        return skills


_global_registry: Optional[GUISkillRegistry] = None


def get_gui_skill_registry() -> GUISkillRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = GUISkillRegistry()
    return _global_registry

