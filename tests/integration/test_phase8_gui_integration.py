from __future__ import annotations

import pytest
from neural.decision_intelligence.gui_integration import (
    async_evaluate_gui_candidates,
    evaluate_gui_candidates,
)
from neural.exceptions import StrictGUIContractViolation


@pytest.mark.asyncio
async def test_gui_pixel_derived_state_evaluation():
    # 1. Valid pixel-derived structured state produced by Phase 7 Visual Grounder
    state = {
        "screen_context": "Settings Dialog — Export Submenu",
        "task_goal": "Save current report as CSV spreadsheet",
        "detected_candidates": [
            {"id": "opt_1", "label": "Export to PDF", "type": "menu_item", "coordinates": [250, 180]},
            {"id": "opt_2", "label": "Export to CSV", "type": "menu_item", "coordinates": [250, 220]},
            {"id": "opt_3", "label": "Cancel", "type": "button", "coordinates": [400, 300]},
        ],
    }

    # Evaluate via async helper
    res = await async_evaluate_gui_candidates(state)

    assert res["selected_id"] == "opt_2"
    assert res["coordinates"] == [250, 220]
    assert res["confidence"] > 0.0
    assert res["latency_ms"] < 50.0

    # Also test sync helper
    res_sync = evaluate_gui_candidates(state)
    assert res_sync["selected_id"] == "opt_2"
    assert res_sync["coordinates"] == [250, 220]


@pytest.mark.asyncio
async def test_strict_gui_contract_violation_prohibited_sources():
    # Attempting to pass DOM / XPath / AT-SPI into Laya must be strictly rejected
    state_with_dom = {
        "screen_context": "Browser View",
        "task_goal": "Click submit",
        "dom": "<button id='submit'>Submit</button>",
        "detected_candidates": [
            {"id": "b1", "label": "Submit", "coordinates": [100, 100]},
            {"id": "b2", "label": "Cancel", "coordinates": [200, 100]},
        ],
    }

    with pytest.raises(StrictGUIContractViolation, match="Prohibited grounding source"):
        await async_evaluate_gui_candidates(state_with_dom)

    # Candidate with xpath
    state_with_xpath = {
        "screen_context": "Window",
        "task_goal": "Click ok",
        "detected_candidates": [
            {"id": "b1", "label": "OK", "xpath": "//button[1]"},
            {"id": "b2", "label": "Cancel"},
        ],
    }

    with pytest.raises(StrictGUIContractViolation, match="Prohibited grounding source"):
        await async_evaluate_gui_candidates(state_with_xpath)


@pytest.mark.asyncio
async def test_gui_bounding_with_high_cardinality():
    # 15 detected candidates on screen must be bounded to <= 12 and not crash
    candidates = [
        {"id": f"btn_{i}", "label": f"Button {i} save csv" if i == 3 else f"Button {i}", "coordinates": [i * 10, i * 10]}
        for i in range(15)
    ]
    state = {
        "screen_context": "Complex Spreadsheet Toolbar",
        "task_goal": "Click save csv button",
        "detected_candidates": candidates,
    }

    res = await async_evaluate_gui_candidates(state)
    assert res["selected_id"] == "btn_3"
    assert res["confidence"] > 0.0


@pytest.mark.asyncio
async def test_prohibited_grounding_case_variations():
    # Uppercase DOM and hyphenated AT-SPI must be caught
    state_upper_dom = {
        "screen_context": "View",
        "task_goal": "Click",
        "DOM": "<div />",
        "detected_candidates": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
    }
    with pytest.raises(StrictGUIContractViolation, match="Prohibited grounding source"):
        await async_evaluate_gui_candidates(state_upper_dom)

    state_at_spi = {
        "screen_context": "View",
        "task_goal": "Click",
        "at-spi": "accessible_object_32",
        "detected_candidates": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
    }
    with pytest.raises(StrictGUIContractViolation, match="Prohibited grounding source"):
        await async_evaluate_gui_candidates(state_at_spi)
