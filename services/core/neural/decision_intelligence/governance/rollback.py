from __future__ import annotations

import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from neural.decision_intelligence.governance.shadow_mode import (
    ShadowModeManager,
    get_shadow_mode_manager,
)
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.governance.rollback")


class RollbackEvent(BaseModel):
    category: str
    reason: str
    reverted_to: str = "shadow_mode"
    timestamp: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RollbackEngine:
    """
    Emergency fallback and rollback engine (Section 13).
    Instantly isolates an errant System 1 category by reverting it to shadow mode
    or routing to safe baseline.
    """

    def __init__(self, shadow_manager: Optional[ShadowModeManager] = None) -> None:
        self.shadow_manager = shadow_manager or get_shadow_mode_manager()
        self._history: list[RollbackEvent] = []

    def execute_rollback(
        self,
        category: str,
        reason: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RollbackEvent:
        logger.warning(
            "Executing emergency rollback to shadow mode",
            category=category,
            reason=reason,
        )

        # Force category back into shadow mode
        self.shadow_manager.set_shadow_mode(category, enabled=True)

        event = RollbackEvent(
            category=category,
            reason=reason,
            reverted_to="shadow_mode",
            metadata=metadata or {},
        )
        self._history.append(event)
        return event

    def list_rollback_history(self) -> list[RollbackEvent]:
        return list(self._history)


_rollback_instance: Optional[RollbackEngine] = None


def get_rollback_engine() -> RollbackEngine:
    global _rollback_instance
    if _rollback_instance is None:
        _rollback_instance = RollbackEngine()
    return _rollback_instance


def reset_rollback_engine() -> None:
    global _rollback_instance
    _rollback_instance = None
