from __future__ import annotations

from typing import Optional

from neural.exceptions import ShadowModeViolationError
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.governance.shadow_mode")


class ShadowModeManager:
    """
    Mandatory Shadow Mode Manager (Section 11).
    Ensures new decision categories run in parallel without mutating production state.
    """

    def __init__(self, default_shadow_mode: bool = True) -> None:
        self.default_shadow_mode = default_shadow_mode
        # Dict of category -> in_shadow_mode boolean
        self._shadow_categories: dict[str, bool] = {
            "routing": False,      # Promoted baseline
            "gui_selection": True, # In shadow mode by default until verified
            "urgency": True,
            "blocker": True,
            "general": True,
        }

    def is_in_shadow_mode(self, category: str) -> bool:
        return self._shadow_categories.get(category.lower(), self.default_shadow_mode)

    def set_shadow_mode(self, category: str, enabled: bool) -> None:
        self._shadow_categories[category.lower()] = enabled
        logger.info("Updated shadow mode state", category=category, enabled=enabled)

    def list_shadow_states(self) -> dict[str, bool]:
        return dict(self._shadow_categories)

    def assert_no_side_effects(self, category: str, attempted_action: str) -> None:
        if self.is_in_shadow_mode(category):
            raise ShadowModeViolationError(
                f"Illegal side-effect in shadow mode: category '{category}' attempted '{attempted_action}'"
            )


_shadow_manager_instance: Optional[ShadowModeManager] = None


def get_shadow_mode_manager() -> ShadowModeManager:
    global _shadow_manager_instance
    if _shadow_manager_instance is None:
        _shadow_manager_instance = ShadowModeManager()
    return _shadow_manager_instance


def reset_shadow_mode_manager() -> None:
    global _shadow_manager_instance
    _shadow_manager_instance = None
