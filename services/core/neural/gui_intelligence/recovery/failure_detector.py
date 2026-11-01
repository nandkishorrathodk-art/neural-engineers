from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from neural.gui_intelligence.models import ScreenFrame, VisualElement
from neural.gui_intelligence.perception.screen_change import ScreenChangeDetector
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.recovery.failure_detector")


@dataclass
class FailureDiagnosis:
    has_failure: bool
    failure_type: Optional[str]  # "STALLED_UI", "BLOCKING_MODAL", "MISSING_CONTROL", "VISUAL_ANOMALY"
    severity: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    details: str
    suggested_recovery_step: int  # 1 to 5 corresponding to Section 8 hierarchy


class GUIFailureDetector:
    """
    GUI Failure & Stall Detector (Section 8 & Section 18).
    Detects UI stalls, unexpected modal dialogs, occluded controls, and visual anomalies.
    """

    def __init__(self, change_detector: Optional[ScreenChangeDetector] = None) -> None:
        self.change_detector = change_detector or ScreenChangeDetector()

    def diagnose(
        self,
        frame_before: ScreenFrame,
        frame_after: ScreenFrame,
        active_dialogs: list[VisualElement],
        action_type: str,
        target_found: bool = True,
    ) -> FailureDiagnosis:
        """
        Analyzes UI state post-action to detect stalls, modal popups, and occlusion failures.
        """
        # 1. Check for blocking modal popups
        if active_dialogs:
            dialog_title = active_dialogs[0].visual_text or "Unexpected Dialog"
            return FailureDiagnosis(
                has_failure=True,
                failure_type="BLOCKING_MODAL",
                severity="HIGH",
                details=f"Unexpected modal popup detected: '{dialog_title}'",
                suggested_recovery_step=4,  # Step 4: Dismiss dialog
            )

        # 2. Check for missing target control
        if not target_found:
            return FailureDiagnosis(
                has_failure=True,
                failure_type="MISSING_CONTROL",
                severity="MEDIUM",
                details="Target UI control is not visible in the current screen frame",
                suggested_recovery_step=1,  # Step 1: Check occlusion
            )

        # 3. Check for UI stall on mutating actions
        if action_type in ("MOUSE_CLICK", "MOUSE_DOUBLE_CLICK", "KEY_HOTKEY"):
            delta = self.change_detector.compute_delta(frame_before, frame_after)
            if delta.magnitude < 0.0001:
                return FailureDiagnosis(
                    has_failure=True,
                    failure_type="STALLED_UI",
                    severity="MEDIUM",
                    details=f"UI failed to transition after {action_type} (delta={delta.magnitude:.6f})",
                    suggested_recovery_step=1,
                )

        return FailureDiagnosis(
            has_failure=False,
            failure_type=None,
            severity="LOW",
            details="Nominal UI state, no failure detected",
            suggested_recovery_step=0,
        )
