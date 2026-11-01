from __future__ import annotations

from typing import Any, Optional

from neural.decision_intelligence.routing.decision_router import (
    IntelligentDecisionRouter,
    get_decision_router,
)
from neural.decision_intelligence.schemas.choice import ChoiceCandidate, ChoiceQuestion
from neural.exceptions import StrictGUIContractViolation
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.gui_integration")


def evaluate_gui_candidates(
    pixel_derived_state: dict[str, Any],
    router: Optional[IntelligentDecisionRouter] = None,
) -> dict[str, Any]:
    """
    Evaluates pixel-derived structured state for GUI candidate selection (Section 6).
    Strictly enforces Phase 7 GUI-Only contract:
    - Zero raw pixel tensors processed directly by Laya
    - Zero DOM inspection or XPath parsing (raises StrictGUIContractViolation)
    - Zero AT-SPI accessibility tree queries (raises StrictGUIContractViolation)
    - Zero motor execution / mouse clicks dispatched inside Laya
    """
    # 1. Prohibited grounding sources check (case-insensitive)
    prohibited_keys = {
        "dom",
        "xpath",
        "at_spi",
        "at-spi",
        "atspi",
        "accessibility_tree",
        "accessibility",
        "html_element",
        "aria",
    }
    state_keys = {str(k).lower() for k in pixel_derived_state.keys()}
    cand_keys = {
        str(k).lower()
        for c in pixel_derived_state.get("detected_candidates", [])
        if isinstance(c, dict)
        for k in c.keys()
    }
    matched = prohibited_keys.intersection(state_keys.union(cand_keys))
    if matched:
        offending = sorted(list(matched))[0]
        raise StrictGUIContractViolation(
            f"Prohibited grounding source detected in GUI candidate evaluation: '{offending}'. "
            "All GUI candidates must originate strictly from screen pixel perception."
        )

    screen_context = pixel_derived_state.get("screen_context", "")
    task_goal = pixel_derived_state.get("task_goal", "")
    detected_candidates = pixel_derived_state.get("detected_candidates", [])

    if len(detected_candidates) < 2:
        raise ValueError("GUI selection requires at least 2 detected candidates from visual perception")

    # Map detected candidates to ChoiceCandidate
    coord_map: dict[str, list[int]] = {}
    candidates: list[ChoiceCandidate] = []

    for item in detected_candidates:
        c_id = item["id"]
        label = item.get("label", c_id)
        candidates.append(
            ChoiceCandidate(
                id=c_id,
                label=label,
                metadata={"type": item.get("type", "ui_element")},
            )
        )
        if "coordinates" in item:
            coord_map[c_id] = item["coordinates"]

    combined_context = f"{task_goal} | Context: {screen_context}"
    active_router = router or get_decision_router()
    bounded_candidates = active_router.candidate_selector.prepare_candidates(candidates, context=combined_context)
    prep_question = ChoiceQuestion(
        context=combined_context,
        candidates=bounded_candidates,
        domain="gui",
    )
    result = active_router.inference_service.adapter.predict_choice(prep_question)

    return {
        "selected_id": result.selected_id,
        "coordinates": coord_map.get(result.selected_id, [0, 0]),
        "confidence": result.confidence,
        "probabilities": result.probabilities,
        "was_escalated": False,
        "latency_ms": result.latency_ms,
        "model_name": result.model_name,
    }


async def async_evaluate_gui_candidates(
    pixel_derived_state: dict[str, Any],
    router: Optional[IntelligentDecisionRouter] = None,
) -> dict[str, Any]:
    """
    Async version of GUI candidate evaluation.
    """
    prohibited_keys = {
        "dom",
        "xpath",
        "at_spi",
        "at-spi",
        "atspi",
        "accessibility_tree",
        "accessibility",
        "html_element",
        "aria",
    }
    state_keys = {str(k).lower() for k in pixel_derived_state.keys()}
    cand_keys = {
        str(k).lower()
        for c in pixel_derived_state.get("detected_candidates", [])
        if isinstance(c, dict)
        for k in c.keys()
    }
    matched = prohibited_keys.intersection(state_keys.union(cand_keys))
    if matched:
        offending = sorted(list(matched))[0]
        raise StrictGUIContractViolation(
            f"Prohibited grounding source detected in GUI candidate evaluation: '{offending}'."
        )

    screen_context = pixel_derived_state.get("screen_context", "")
    task_goal = pixel_derived_state.get("task_goal", "")
    detected_candidates = pixel_derived_state.get("detected_candidates", [])

    if len(detected_candidates) < 2:
        raise ValueError("GUI selection requires at least 2 detected candidates from visual perception")

    coord_map: dict[str, list[int]] = {}
    candidates: list[ChoiceCandidate] = []

    for item in detected_candidates:
        c_id = item["id"]
        label = item.get("label", c_id)
        candidates.append(
            ChoiceCandidate(
                id=c_id,
                label=label,
                metadata={"type": item.get("type", "ui_element")},
            )
        )
        if "coordinates" in item:
            coord_map[c_id] = item["coordinates"]

    combined_context = f"{task_goal} | Context: {screen_context}"
    active_router = router or get_decision_router()
    bounded_candidates = active_router.candidate_selector.prepare_candidates(candidates, context=combined_context)
    question = ChoiceQuestion(
        context=combined_context,
        candidates=bounded_candidates,
        domain="gui",
    )

    result = await active_router.route_choice(question)

    return {
        "selected_id": result.selected_id,
        "coordinates": coord_map.get(result.selected_id, [0, 0]),
        "confidence": result.confidence,
        "probabilities": result.probabilities,
        "was_escalated": result.was_escalated,
        "latency_ms": result.latency_ms,
        "model_name": result.model_name,
    }
