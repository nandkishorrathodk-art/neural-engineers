"""Unit tests for the container-bound GUI worker runtime.

These lock in the fixes that made real-desktop GUI execution work end to end:

* ``move_cursor`` must not use ``xdotool mousemove --sync``; that flag blocks
  forever when the pointer is already at the target (no motion event arrives),
  which wedged the worker.
* The step planner must click the field before typing, because a real desktop
  has no focus to inherit and the keyboard focus gate fails closed otherwise.
* The worker engine must reuse the last click coordinate for a coordinate-less
  ``KEY_TYPE`` step so the focus gate has a concrete target.
"""

from __future__ import annotations

from typing import Any

import pytest
from neural.gui_intelligence.perception.ocr_runtime import TesseractOCREngine
from neural.gui_intelligence.runtime.desktop_driver import ContainerDesktopDriver
from neural.gui_intelligence.runtime.planner import derive_gui_steps


class _RecordingManager:
    """Stand-in for ContainerManager that records argv and returns canned output."""

    def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.stdout = stdout
        self.exit_code = exit_code

    def execute_command_sync(self, docker_id: str, command: list[str], workdir: str | None = None) -> dict[str, Any]:
        self.commands.append(list(command))
        return {"exit_code": self.exit_code, "stdout": self.stdout, "stderr": ""}


def test_move_cursor_does_not_use_blocking_sync():
    manager = _RecordingManager()
    driver = ContainerDesktopDriver(manager, "ancos-desktop-anc-emp-test", display=":1")

    driver.move_cursor(300, 200)

    shell = manager.commands[-1][-1]
    assert "mousemove" in shell
    assert "300" in shell and "200" in shell
    assert "--sync" not in shell


def test_driver_assert_available_probes_display_and_binaries():
    manager = _RecordingManager(exit_code=1)
    driver = ContainerDesktopDriver(manager, "ancos-desktop-anc-emp-test", display=":1")

    from neural.gui_intelligence.runtime.errors import DesktopRuntimeUnavailable

    with pytest.raises(DesktopRuntimeUnavailable):
        driver.assert_available()
    assert driver.available is False


def test_planner_clicks_field_before_typing():
    steps = derive_gui_steps(
        "Fill the form",
        "Click the 'Username' field and type 'alice' into the username field",
    )
    action_types = [step["action_type"] for step in steps]
    assert action_types[:2] == ["MOUSE_CLICK", "KEY_TYPE"]
    assert steps[0]["target"] == "Username"
    assert steps[1]["text"] == "alice"


def test_planner_click_only_task():
    steps = derive_gui_steps("Open the editor", "Click the 'Applications' menu to open it")
    assert steps == [{"action_type": "MOUSE_CLICK", "target": "Applications", "confidence": 0.95}]


def test_planner_reads_unquoted_click_label():
    steps = derive_gui_steps(
        "Open File Manager from Application Menu",
        "Using the graphical desktop only, open the application menu and click the "
        "File Manager icon to open it, then confirm a window is visible.",
        "Desktop GUI interaction to launch File Manager application.",
    )
    assert steps == [
        {"action_type": "MOUSE_CLICK", "target": "Applications", "confidence": 0.95},
        {"action_type": "MOUSE_CLICK", "target": "File Manager", "confidence": 0.95},
    ]


def test_planner_falls_back_to_settle_without_groundable_verb():
    steps = derive_gui_steps("Inspect", "Observe the current desktop state")
    assert steps == [{"action_type": "WAIT_SETTLE", "confidence": 0.99}]


def test_planner_routes_app_concept_through_launcher_to_real_label():
    """A concept name ("text editor") is not on screen; route to the menu label."""
    steps = derive_gui_steps(
        "Open text editor and create new document",
        "Launch the default text editor application via GUI and open a new blank document window.",
        "Use only GUI interactions to start the text editor application.",
    )
    assert steps == [
        {"action_type": "MOUSE_CLICK", "target": "Applications", "confidence": 0.95},
        {"action_type": "MOUSE_CLICK", "target": "Accessories", "confidence": 0.95},
        {"action_type": "MOUSE_CLICK", "target": "Mousepad", "confidence": 0.95},
    ]


def test_planner_extracts_unquoted_typed_payload():
    steps = derive_gui_steps(
        "Launch text editor and type marker",
        "Open the text editor on the desktop and type the marker GUI_LIFECYCLE_42 into the document.",
    )
    assert steps[-1] == {"action_type": "KEY_TYPE", "text": "GUI_LIFECYCLE_42", "confidence": 0.95}
    assert [s["target"] for s in steps[:3]] == ["Applications", "Accessories", "Mousepad"]


def test_planner_does_not_treat_menu_click_title_as_app_launch():
    """A short title naming an editor must not hijack an explicit menu click."""
    steps = derive_gui_steps("Open the editor", "Click the 'Applications' menu to open it")
    assert steps == [{"action_type": "MOUSE_CLICK", "target": "Applications", "confidence": 0.95}]


def test_planner_targets_leftmost_app_and_skips_negated_exclusion():
    """A later exclusion ("without a terminal") must not become the launch target."""
    steps = derive_gui_steps(
        "Open browser",
        "Open the web browser application, without using a terminal.",
    )
    assert steps == [
        {"action_type": "MOUSE_CLICK", "target": "Applications", "confidence": 0.95},
        {"action_type": "MOUSE_CLICK", "target": "Web Browser", "confidence": 0.95},
    ]


def test_engine_reuses_last_click_for_coordinateless_key_type():
    from neural.gui_intelligence.worker import GuiStepPlan, GuiWorkerEngine

    engine = GuiWorkerEngine(container_manager=object(), require_real_desktop=False)
    request = engine._to_request(
        "ANC-EMP-TEST",
        GuiStepPlan(action_type="KEY_TYPE", text="hello"),
        frame=None,
        grounder=None,
        fallback_coordinates=(320, 240),
    )
    assert request.coordinates == (320, 240)
    assert request.text_content == "hello"


def test_engine_key_type_without_focus_target_has_no_coordinates():
    from neural.gui_intelligence.worker import GuiStepPlan, GuiWorkerEngine

    engine = GuiWorkerEngine(container_manager=object(), require_real_desktop=False)
    request = engine._to_request(
        "ANC-EMP-TEST",
        GuiStepPlan(action_type="KEY_TYPE", text="hello"),
        frame=None,
        grounder=None,
        fallback_coordinates=None,
    )
    assert request.coordinates is None


_TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def test_ocr_tsv_parse_emits_word_and_line_boxes():
    tsv = "\n".join([
        _TSV_HEADER,
        "5\t1\t1\t1\t1\t1\t9\t65\t22\t11\t86\tFile",
        "5\t1\t1\t1\t1\t2\t48\t65\t24\t10\t86\tEdit",
        "5\t1\t1\t1\t1\t3\t89\t65\t43\t11\t95\tSearch",
    ])
    regions = TesseractOCREngine._parse_tsv(tsv)
    by_text = {r.text: r for r in regions}
    # Word-level box is preserved for precise single-label clicks.
    assert by_text["File"].bounding_box == (9, 65, 31, 76)
    # Line-level aggregate lets a multi-word phrase match as one region.
    assert by_text["File Edit Search"].bounding_box == (9, 65, 132, 76)


def test_ocr_tsv_parse_ignores_empty_and_negative_conf():
    tsv = "\n".join([
        _TSV_HEADER,
        "5\t1\t1\t1\t1\t1\t0\t0\t0\t0\t-1\t",
        "5\t1\t1\t1\t1\t1\t10\t10\t5\t5\t-1\tnoise",
        "5\t1\t1\t1\t1\t2\t20\t20\t5\t5\t90\tok",
    ])
    regions = TesseractOCREngine._parse_tsv(tsv)
    assert [r.text for r in regions] == ["ok", "ok"]


def test_engine_refuses_blind_click_when_grounding_returns_negative():
    from neural.gui_intelligence.worker import GuiStepPlan, GuiWorkerEngine

    class _BlindGrounder:
        def ground_query(self, frame, target):
            return type("T", (), {"coordinates": (-1, -1), "confidence": 0.1, "is_confident": False})()

    engine = GuiWorkerEngine(container_manager=object(), require_real_desktop=False)
    from neural.gui_intelligence.worker import GuiWorkerExecutionError

    with pytest.raises(GuiWorkerExecutionError):
        engine._to_request(
            "ANC-EMP-TEST",
            GuiStepPlan(action_type="MOUSE_CLICK", target="Missing"),
            frame=None,
            grounder=_BlindGrounder(),
        )
