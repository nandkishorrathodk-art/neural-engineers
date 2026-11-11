from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from neural.exceptions import VisualUncertaintyError
from neural.gui_intelligence.models import ScreenFrame, VisualElement
from neural.gui_intelligence.perception.object_detection import UIObjectDetector
from neural.gui_intelligence.perception.pixel_ocr import PixelOCREngine
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.perception.visual_grounding")


@dataclass
class GroundedTarget:
    coordinates: tuple[int, int]  # (x, y) center pixel coordinates
    bounding_box: tuple[int, int, int, int]  # (xmin, ymin, xmax, ymax)
    confidence: float
    variance: float  # spatial uncertainty sigma^2
    matched_element: Optional[VisualElement]
    query: str
    grounding_latency_ms: float

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

    def ground_query(
        self,
        frame: ScreenFrame,
        query: str,
        expected_element_type: Optional[str] = None,
        require_confidence: bool = False,
    ) -> GroundedTarget:
        """
        Grounds a text query to pixel coordinates on the active screen frame.
        Fuses raster OCR text matching with UI bounding box detection.
        """
        start = time.perf_counter()
        query_clean = query.strip()
        query_lower = query_clean.lower()

        # 1. OCR text search over raster pixels
        ocr_matches = self.ocr_engine.find_text(frame, query_clean, fuzzy=True, min_confidence=0.6)

        # 2. Detected UI objects
        ui_elements = self.object_detector.detect(frame)

        best_target: Optional[GroundedTarget] = None
        best_score: float = -1.0

        # Try to match OCR text directly
        for match in ocr_matches:
            center_x, center_y = match.center
            box = match.bounding_box

            # Find enclosing or nearby UI element
            enclosing = None
            for elem in ui_elements:
                if elem.contains_point(center_x, center_y):
                    enclosing = elem
                    break

            # Multi-cue confidence fusion: base OCR + text exactness + element type affinity
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

            variance = max(0.01, 1.0 - score)
            if score > best_score:
                best_score = score
                latency_ms = (time.perf_counter() - start) * 1000.0
                best_target = GroundedTarget(
                    coordinates=(center_x, center_y),
                    bounding_box=box,
                    confidence=score,
                    variance=variance,
                    matched_element=enclosing,
                    query=query_clean,
                    grounding_latency_ms=latency_ms,
                )

        # If no OCR direct hit, check UI elements by text or type descriptor
        if best_target is None or best_target.confidence < self.CONFIDENCE_THRESHOLD:
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
                    variance = max(0.01, 1.0 - score)
                    latency_ms = (time.perf_counter() - start) * 1000.0
                    best_target = GroundedTarget(
                        coordinates=(cx, cy),
                        bounding_box=elem.bounding_box,
                        confidence=score,
                        variance=variance,
                        matched_element=elem,
                        query=query_clean,
                        grounding_latency_ms=latency_ms,
                    )

        # Fallback if nothing matched: Never blindly assign screen center coordinates
        if best_target is None:
            latency_ms = (time.perf_counter() - start) * 1000.0
            best_target = GroundedTarget(
                coordinates=(-1, -1),
                bounding_box=(0, 0, 0, 0),
                confidence=0.10,
                variance=0.90,
                matched_element=None,
                query=query_clean,
                grounding_latency_ms=latency_ms,
            )

        if require_confidence and not best_target.is_confident:
            raise VisualUncertaintyError(
                f"Visual grounding failed confidence threshold for '{query_clean}': "
                f"confidence={best_target.confidence:.2f} < {self.CONFIDENCE_THRESHOLD}, "
                f"variance={best_target.variance:.2f}"
            )

        return best_target
