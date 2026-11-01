from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from neural.gui_intelligence.models import ScreenFrame
from neural.gui_intelligence.perception.pixel_ocr import PixelOCREngine
from neural.gui_intelligence.perception.screen_change import ScreenChangeDetector
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.verification.visual_verifier")


@dataclass
class VisualVerificationResult:
    passed: bool
    score: float  # 0.0 to 1.0
    delta_magnitude: float
    postcondition_checked: str
    details: str
    screenshot_hash: str


class IndependentVisualVerifier:
    """
    Independent Visual Verifier & Postcondition Judge (Section 10.1).
    Enforces the zero success-by-decree rule.
    Verifies that the requested outcome visually appears in the postcondition screenshot:
    - Calculates pixel-difference heatmap H = |S_final - S_initial|
    - Scans raster pixels for expected strings/controls via PixelOCREngine
    - Evaluates postcondition criteria out-of-band
    """

    def __init__(
        self,
        ocr_engine: Optional[PixelOCREngine] = None,
        change_detector: Optional[ScreenChangeDetector] = None,
    ) -> None:
        self.ocr_engine = ocr_engine or PixelOCREngine()
        self.change_detector = change_detector or ScreenChangeDetector()

    def verify_postcondition(
        self,
        frame_initial: ScreenFrame,
        frame_final: ScreenFrame,
        expected_text: Optional[str] = None,
        min_delta_magnitude: float = 0.0001,
        forbidden_text: Optional[str] = None,
    ) -> VisualVerificationResult:
        """
        Verifies final task deliverable against visual evidence.
        """
        # 1. Optical delta check: Confirm visual change took place
        delta = self.change_detector.compute_delta(frame_initial, frame_final)

        # 2. Text postcondition check over raster pixels
        text_passed = True
        details_list = []

        if expected_text:
            matches = self.ocr_engine.find_text(frame_final, expected_text, fuzzy=True, min_confidence=0.7)
            if matches:
                details_list.append(f"Expected text '{expected_text}' visually verified in screen pixels")
            else:
                text_passed = False
                details_list.append(f"Expected text '{expected_text}' NOT found in final screenshot")

        if forbidden_text:
            matches = self.ocr_engine.find_text(frame_final, forbidden_text, fuzzy=True, min_confidence=0.7)
            if matches:
                text_passed = False
                details_list.append(f"Forbidden error text '{forbidden_text}' detected in screenshot")

        delta_passed = delta.magnitude >= min_delta_magnitude
        if not delta_passed and min_delta_magnitude > 0:
            details_list.append(f"Visual delta magnitude {delta.magnitude:.6f} below minimum {min_delta_magnitude}")

        passed = text_passed and delta_passed
        score = 1.0 if passed else 0.0

        return VisualVerificationResult(
            passed=passed,
            score=score,
            delta_magnitude=delta.magnitude,
            postcondition_checked=expected_text or "Visual state transition",
            details="; ".join(details_list) if details_list else "Visual postcondition verified",
            screenshot_hash=frame_final.frame_hash,
        )
