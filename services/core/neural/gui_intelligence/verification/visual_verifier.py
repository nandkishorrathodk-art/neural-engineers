from __future__ import annotations

import re
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
        expected_postcondition: Optional[str] = None,
    ) -> VisualVerificationResult:
        """
        Verifies final task deliverable against visual evidence.
        Semantically evaluates natural-language postconditions, optical pixel deltas,
        and raster text evidence.
        """
        # 1. Optical delta check: Confirm visual change took place
        delta = self.change_detector.compute_delta(frame_initial, frame_final)

        # 2. Text postcondition check over raster pixels
        text_passed = True
        details_list = []

        if expected_text:
            matches = self.ocr_engine.find_text(
                frame_final, expected_text, fuzzy=True, min_confidence=0.7
            )
            if matches:
                details_list.append(
                    f"Expected text '{expected_text}' visually verified in screen pixels"
                )
            else:
                text_passed = False
                details_list.append(
                    f"Expected text '{expected_text}' NOT found in final screenshot"
                )

        if forbidden_text:
            matches = self.ocr_engine.find_text(
                frame_final, forbidden_text, fuzzy=True, min_confidence=0.7
            )
            if matches:
                text_passed = False
                details_list.append(
                    f"Forbidden error text '{forbidden_text}' detected in screenshot"
                )

        # 3. Semantic understanding of natural-language postcondition
        postcondition_passed = True
        if expected_postcondition:
            post_lower = expected_postcondition.lower().strip()

            # Quoted substrings in postcondition (e.g. "Save As...", 'Document1')
            quoted_strings = re.findall(r"['\"](.*?)['\"]", expected_postcondition)
            for qstr in quoted_strings:
                q_matches = self.ocr_engine.find_text(
                    frame_final, qstr, fuzzy=True, min_confidence=0.7
                )
                if q_matches:
                    details_list.append(
                        f"Semantic target '{qstr}' visually confirmed in postcondition"
                    )
                else:
                    postcondition_passed = False
                    details_list.append(
                        f"Semantic target '{qstr}' missing from postcondition screen"
                    )

            # Check for negative/dismissal postconditions: "closed", "dismissed", "removed"
            negative_keywords = ["closed", "dismissed", "removed", "hidden"]
            if any(k in post_lower for k in negative_keywords):
                # Verify that dialog or modal text is absent from final screen pixels
                if "dialog" in post_lower or "modal" in post_lower or "alert" in post_lower:
                    modal_keywords = [
                        "confirm action",
                        "system alert",
                        "error alert",
                        "do you want to save",
                    ]
                    modal_detected = False
                    for mkw in modal_keywords:
                        if self.ocr_engine.find_text(
                            frame_final, mkw, fuzzy=True, min_confidence=0.7
                        ):
                            modal_detected = True
                            break
                    if modal_detected:
                        postcondition_passed = False
                        details_list.append(
                            "Modal dialog text remained visible in postcondition screen"
                        )
                    else:
                        details_list.append(
                            "Modal/dialog dismissal confirmed from visual raster evidence"
                        )

            # Check for positive state indicators if no quoted string was extracted
            elif not quoted_strings and expected_text is None:
                # Extract key semantic nouns/verbs/numerics from postcondition
                stopwords = {
                    "a",
                    "an",
                    "the",
                    "in",
                    "on",
                    "at",
                    "to",
                    "for",
                    "of",
                    "and",
                    "or",
                    "is",
                    "are",
                    "was",
                    "were",
                    "be",
                    "been",
                    "with",
                    "from",
                    "by",
                    "as",
                    "visible",
                    "open",
                    "opened",
                    "appears",
                    "active",
                    "focused",
                    "field",
                }
                words = [
                    w
                    for w in re.findall(r"[a-zA-Z0-9_]+", post_lower)
                    if (len(w) >= 3 or w.isdigit()) and w not in stopwords
                ]
                matched_words = []
                for w in words:
                    matches = self.ocr_engine.find_text(
                        frame_final, w, fuzzy=True, min_confidence=0.7
                    )
                    if matches:
                        matched_words.append(w)
                if words and not matched_words and delta.magnitude < min_delta_magnitude:
                    postcondition_passed = False
                    details_list.append(
                        f"Semantic terms '{words[:3]}' not observed in screen raster"
                    )
                elif matched_words:
                    details_list.append(
                        f"Observed semantic terms in screen raster: {matched_words}"
                    )

        delta_passed = delta.magnitude >= min_delta_magnitude
        # A verified on-screen deliverable is sufficient evidence on its own: typing a
        # short marker (or acting on an already-open window) alters very few pixels,
        # so requiring a whole-frame delta floor would reject a correct result.
        if not delta_passed and min_delta_magnitude > 0 and not (expected_text and text_passed):
            details_list.append(
                f"Visual delta magnitude {delta.magnitude:.6f} below minimum {min_delta_magnitude}"
            )
        elif not delta_passed and expected_text and text_passed:
            delta_passed = True

        passed = text_passed and postcondition_passed and delta_passed
        score = 1.0 if passed else 0.0

        target_checked = expected_postcondition or expected_text or "Visual state transition"
        return VisualVerificationResult(
            passed=passed,
            score=score,
            delta_magnitude=delta.magnitude,
            postcondition_checked=target_checked,
            details="; ".join(details_list) if details_list else "Visual postcondition verified",
            screenshot_hash=frame_final.frame_hash,
        )
