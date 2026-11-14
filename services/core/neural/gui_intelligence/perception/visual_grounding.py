from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from neural.exceptions import VisualUncertaintyError
from neural.gui_intelligence.models import ScreenFrame, VisualElement
from neural.gui_intelligence.perception.object_detection import UIObjectDetector
from neural.gui_intelligence.perception.ocr_cache import OCRRegion
from neural.gui_intelligence.perception.pixel_ocr import PixelOCREngine
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.perception.visual_grounding")

# Context margin added around a previously grounded box before it is reused as a
# region hint, so nearby labels sharing a row are not cropped out.
_REGION_CONTEXT_MARGIN = 28


@dataclass
class GroundedTarget:
    coordinates: tuple[int, int]  # (x, y) center pixel coordinates
    bounding_box: tuple[int, int, int, int]  # (xmin, ymin, xmax, ymax)
    confidence: float
    variance: float  # spatial uncertainty sigma^2
    matched_element: Optional[VisualElement]
    query: str
    grounding_latency_ms: float
    # Telemetry (Stage 2). Defaults keep older callers working.
    grounding_source: str = "full_frame"  # "region" | "full_frame" | "none"
    target_candidates: int = 0
    region: Optional[tuple[int, int, int, int]] = None

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.88 and self.variance <= 0.12


class VisualGrounder:
    """
    Visual Grounder (Section 11.1 ANCOS-Grounder).
    Grounds natural language text queries or visual target descriptors to pixel-accurate
    screen coordinates (x, y) with associated spatial uncertainty variance sigma^2.
    Enforces the confidence escort threshold (confidence >= 0.88, uncertainty <= 0.12).
    """

    CONFIDENCE_THRESHOLD: float = 0.88
    UNCERTAINTY_THRESHOLD: float = 0.12

    def __init__(
        self,
        ocr_engine: Optional[PixelOCREngine] = None,
        object_detector: Optional[UIObjectDetector] = None,
    ) -> None:
        self.ocr_engine = ocr_engine or PixelOCREngine()
        self.object_detector = object_detector or UIObjectDetector()
        # The most recent grounded target, so a caller can reuse its box as a
        # region hint. None until a grounding has run.
        self.last_target: Optional[GroundedTarget] = None

    def ground_query(
        self,
        frame: ScreenFrame,
        query: str,
        expected_element_type: Optional[str] = None,
        require_confidence: bool = False,
        region_hint: Optional[OCRRegion] = None,
    ) -> GroundedTarget:
        """
        Grounds a text query to pixel coordinates on the active screen frame.
        Fuses raster OCR text matching with UI bounding box detection.

        When a trustworthy ``region_hint`` is supplied, the target is first looked
        for inside that region (a fraction of the screen), which is far cheaper
        than a full-frame read. The region result is accepted only when it is
        confident; otherwise the grounder falls back to a full-frame read, so a
        stale or wrong hint can never produce a wrong target.
        """
        start = time.perf_counter()
        query_clean = query.strip()
        query_lower = query_clean.lower()
        ui_elements = self.object_detector.detect(frame)

        # 1. Try a targeted region first when a hint is available. A region is a
        #    fraction of the screen and far cheaper, but it is only trusted when the
        #    *fused* grounding confidence clears the threshold; otherwise we fall
        #    back to a full-frame read, so a stale or wrong hint can never produce a
        #    wrong target.
        best_target: Optional[GroundedTarget] = None
        grounding_source = "full_frame"
        region_used: Optional[tuple[int, int, int, int]] = None
        target_candidates = 0
        if region_hint is not None and hasattr(self.ocr_engine, "find_text_in_region"):
            region_matches = self.ocr_engine.find_text_in_region(
                frame, query_clean, region_hint, fuzzy=True, min_confidence=0.6
            )
            candidate = self._select_ocr_target(
                query_lower, region_matches, ui_elements, expected_element_type
            )
            containment = getattr(
                self.ocr_engine, "has_containment_match", lambda *_: False
            )(query_clean, region_matches)
            # Only the region's *own* OCR reading may justify the region path. A
            # UI-element match would be detected from the whole frame anyway, so it
            # must not be credited to the region (and would mislabel the source).
            if candidate is not None and (
                candidate.confidence >= self.CONFIDENCE_THRESHOLD or containment
            ):
                best_target = candidate
                grounding_source = "region"
                region_used = region_hint.to_tuple()
                target_candidates = len(region_matches)

        # 2. Full-frame OCR search (always the fallback path).
        if best_target is None:
            ocr_matches = self.ocr_engine.find_text(
                frame, query_clean, fuzzy=True, min_confidence=0.6
            )
            target_candidates = len(ocr_matches)
            best_target = self._select_ocr_target(
                query_lower, ocr_matches, ui_elements, expected_element_type
            )
            if best_target is None or best_target.confidence < self.CONFIDENCE_THRESHOLD:
                ui_target = self._select_ui_element_target(
                    query_lower, ui_elements, expected_element_type
                )
                if ui_target is not None and (
                    best_target is None or ui_target.confidence > best_target.confidence
                ):
                    best_target = ui_target

        # 3. Fallback if nothing matched: Never blindly assign screen center coordinates
        if best_target is None:
            best_target = GroundedTarget(
                coordinates=(-1, -1),
                bounding_box=(0, 0, 0, 0),
                confidence=0.10,
                variance=0.90,
                matched_element=None,
                query=query_clean,
                grounding_latency_ms=0.0,
                grounding_source="none",
                target_candidates=target_candidates,
                region=region_used,
            )

        latency_ms = (time.perf_counter() - start) * 1000.0
        best_target.query = query_clean
        best_target.grounding_latency_ms = latency_ms
        best_target.grounding_source = grounding_source if best_target.confidence > 0.10 else "none"
        best_target.target_candidates = target_candidates
        best_target.region = region_used

        if require_confidence and not best_target.is_confident:
            raise VisualUncertaintyError(
                f"Visual grounding failed confidence threshold for '{query_clean}': "
                f"confidence={best_target.confidence:.2f} < {self.CONFIDENCE_THRESHOLD}, "
                f"variance={best_target.variance:.2f}"
            )

        self.last_target = best_target
        return best_target

    def _select_ocr_target(
        self,
        query_lower: str,
        ocr_matches: list,
        ui_elements: list,
        expected_element_type: Optional[str] = None,
    ) -> Optional[GroundedTarget]:
        """Best target derived from OCR text matches (fused with UI element type).

        Returns None when no OCR match exists. A UI-element-only match is handled
        separately by ``_select_ui_element_target`` so the region path can never be
        credited to a whole-frame UI detection.
        """
        best: Optional[GroundedTarget] = None
        best_score = -1.0

        for match in ocr_matches:
            center_x, center_y = match.center
            box = match.bounding_box

            enclosing = None
            for elem in ui_elements:
                if elem.contains_point(center_x, center_y):
                    enclosing = elem
                    break

            score = match.confidence
            if match.text.strip().lower() == query_lower:
                score = min(1.0, score + 0.05)
            elif query_lower in match.text.strip().lower().split():
                score = score * 0.90

            if enclosing:
                if expected_element_type and enclosing.element_type == expected_element_type:
                    score = min(1.0, score + 0.05)
                elif expected_element_type and enclosing.element_type != expected_element_type:
                    score = max(0.01, score - 0.05)
                else:
                    score = min(1.0, score + 0.02)

            if score > best_score:
                best_score = score
                best = GroundedTarget(
                    coordinates=(center_x, center_y),
                    bounding_box=box,
                    confidence=score,
                    variance=max(0.01, 1.0 - score),
                    matched_element=enclosing,
                    query=query_lower,
                    grounding_latency_ms=0.0,
                    target_candidates=len(ocr_matches),
                )

        return best

    def _select_ui_element_target(
        self,
        query_lower: str,
        ui_elements: list,
        expected_element_type: Optional[str] = None,
    ) -> Optional[GroundedTarget]:
        """Best target from detected UI elements when OCR text did not match."""
        best: Optional[GroundedTarget] = None
        best_score = -1.0

        for elem in ui_elements:
            elem_text = (elem.visual_text or "").lower()
            matched = False
            score = elem.confidence

            if elem_text and (query_lower in elem_text or elem_text in query_lower):
                matched = True
                score = min(1.0, elem.confidence + 0.05)
            elif expected_element_type and elem.element_type == expected_element_type:
                matched = True
                score = elem.confidence * 0.90
            elif elem_text and any(word in elem_text for word in query_lower.split()):
                matched = True
                score = elem.confidence * 0.85

            if matched and score > best_score:
                best_score = score
                cx, cy = elem.center()
                best = GroundedTarget(
                    coordinates=(cx, cy),
                    bounding_box=elem.bounding_box,
                    confidence=score,
                    variance=max(0.01, 1.0 - score),
                    matched_element=elem,
                    query=query_lower,
                    grounding_latency_ms=0.0,
                )

        return best

    @staticmethod
    def region_from_box(
        bounding_box: tuple[int, int, int, int],
        screen: tuple[int, int],
        margin: int = _REGION_CONTEXT_MARGIN,
    ) -> Optional[OCRRegion]:
        """Context-expanded region around a previously grounded box, or None."""
        return OCRRegion.from_bounding_box(bounding_box, margin, screen[0], screen[1])
