from __future__ import annotations

import re
from typing import Any

from neural.exceptions import GUIActionBlockedError, StrictGUIContractViolation
from neural.gui_intelligence.models import GUIActionRequest
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.safety.gui_action_policy")


class GUIActionPolicy:
    """
    GUI Action Safety Policy & Risk Classifier (Section 2 & Section 17).
    Enforces:
    1. Strict GUI-Only Interaction Contract:
       - Strictly blocks DOM inspection, DevTools (CDP), AT-SPI accessibility trees,
         direct database manipulation, or shell command bypasses for GUI tasks.
    2. Action Risk Classification:
       - LOW: Safe mouse movements, scrolling, viewing, focus clicks.
       - MEDIUM: Normal text entry, button clicks, tab switches.
       - HIGH: System setting modifications, file overwrites.
       - CRITICAL: Destructive operations (database drops, disk wipe, production deploys).
    """

    PROHIBITED_SHORTCUT_PATTERNS: list[str] = [
        r"document\.querySelector",
        r"document\.getElementById",
        r"eval\(",
        r"playwright",
        r"puppeteer",
        r"cdp\.",
        r"at-spi",
        r"accessibility_tree",
        r"xdotool\s+key",
        r"curl\s+",
        r"wget\s+",
        r"__import__\('sqlite3'\)",
    ]

    DESTRUCTIVE_KEYWORDS: set[str] = {
        "drop_table",
        "delete_database",
        "drop database",
        "truncate",
        "wipe_disk",
        "rm -rf",
        "purge_all",
        "deploy_production",
        "format drive",
        "destroy_cluster",
        "wipe",
    }

    def validate_gui_contract(self, execution_context: str | dict[str, Any]) -> None:
        """
        Validates that execution context does not contain forbidden non-GUI shortcuts.
        Raises StrictGUIContractViolation if forbidden pattern is detected.
        """
        raw_text = str(execution_context).lower()
        for pat in self.PROHIBITED_SHORTCUT_PATTERNS:
            if re.search(pat, raw_text, re.IGNORECASE):
                logger.error("Strict GUI-Only contract violation detected", pattern=pat)
                raise StrictGUIContractViolation(
                    f"Execution violates Strict GUI-Only Contract: prohibited shortcut '{pat}' detected."
                )

    def classify_risk(self, request: GUIActionRequest) -> str:
        """
        Classifies risk level: LOW, MEDIUM, HIGH, CRITICAL.
        """
        # Check text content for destructive commands
        if request.text_content:
            text_lower = request.text_content.lower()
            for kw in self.DESTRUCTIVE_KEYWORDS:
                if kw in text_lower:
                    return "CRITICAL"

        # Check explicit risk_level on request
        if request.risk_level in ("CRITICAL", "HIGH"):
            return request.risk_level

        # Double clicks and drags can be medium risk
        if request.action_type in ("MOUSE_DOUBLE_CLICK", "MOUSE_DRAG"):
            return "MEDIUM"

        if request.action_type == "KEY_TYPE":
            return "MEDIUM"

        return "LOW"

    def check_permission(self, request: GUIActionRequest) -> bool:
        """
        Verifies whether request is permitted under standard execution policies.
        Raises GUIActionBlockedError if blocked.
        """
        risk = self.classify_risk(request)
        if risk == "CRITICAL" and request.risk_level != "CRITICAL":
            raise GUIActionBlockedError(
                f"Action '{request.action_id}' classified as CRITICAL risk and was not flagged with elevated permissions."
            )
        return True
