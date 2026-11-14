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
from neural.gui_intelligence.action.keyboard_controller import canonicalize_hotkey
from neural.gui_intelligence.models import GUIActionRequest
from neural.gui_intelligence.perception.ocr_runtime import TesseractOCREngine
from neural.gui_intelligence.runtime.desktop_driver import ContainerDesktopDriver
from neural.gui_intelligence.runtime.planner import derive_gui_steps, expected_onscreen_text


class _RecordingManager:
    """Stand-in for ContainerManager that records argv and returns canned output."""

    def __init__(self, stdout: str = "", exit_code: int = 0) -> None:
        self.commands: list[list[str]] = []
        self.stdout = stdout
        self.exit_code = exit_code

    def execute_command_sync(
        self, docker_id: str, command: list[str], workdir: str | None = None
    ) -> dict[str, Any]:
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


def test_driver_reset_editor_state_clears_mousepad_crash_recovery_state():
    """A prior unclean desktop shutdown leaves Mousepad autosave state that makes the
    next launch show a modal "restore data?" dialog. That dialog holds keyboard focus,
    so the plan's Ctrl+O never reaches the editor and the GUI edit silently no-ops.
    The driver must clear the stale state before the choreography starts."""
    manager = _RecordingManager()
    driver = ContainerDesktopDriver(manager, "ancos-desktop-anc-emp-test", display=":1")

    driver.reset_editor_state()

    shell = manager.commands[-1][-1]
    assert "/home/employee/.local/share/Mousepad" in shell
    assert "/home/employee/.config/mousepad" in shell
    assert "pkill" in shell


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


def test_planner_extracts_payload_after_input_verb_and_exact_string_filler():
    """The Boss LLM phrases text entry as "input the exact string X into ..."; the
    planner must still emit the KEY_TYPE step or the worker never types the marker."""
    steps = derive_gui_steps(
        "Open text editor and type GUI-OK marker",
        "Launch the default text editor application via the graphical desktop interface "
        "and input the exact string GUI-OK into a new document.",
        "Desktop automation using only graphical interface controls; no command-line input.",
    )
    assert steps[-1] == {"action_type": "KEY_TYPE", "text": "GUI-OK", "confidence": 0.95}
    assert [s["target"] for s in steps[:3]] == ["Applications", "Accessories", "Mousepad"]


def test_expected_onscreen_text_returns_typed_marker():
    steps = derive_gui_steps(
        "Open text editor and type GUI-OK marker",
        "Launch the default text editor application via the graphical desktop interface "
        "and input the exact string GUI-OK into a new document.",
    )
    assert expected_onscreen_text(steps) == "GUI-OK"


def test_expected_onscreen_text_none_without_text_entry():
    steps = derive_gui_steps("Open the editor", "Click the 'Applications' menu to open it")
    assert expected_onscreen_text(steps) is None


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


_TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def test_ocr_tsv_parse_emits_word_and_line_boxes():
    tsv = "\n".join(
        [
            _TSV_HEADER,
            "5\t1\t1\t1\t1\t1\t9\t65\t22\t11\t86\tFile",
            "5\t1\t1\t1\t1\t2\t48\t65\t24\t10\t86\tEdit",
            "5\t1\t1\t1\t1\t3\t89\t65\t43\t11\t95\tSearch",
        ]
    )
    regions = TesseractOCREngine._parse_tsv(tsv)
    by_text = {r.text: r for r in regions}
    # Word-level box is preserved for precise single-label clicks.
    assert by_text["File"].bounding_box == (9, 65, 31, 76)
    # Line-level aggregate lets a multi-word phrase match as one region.
    assert by_text["File Edit Search"].bounding_box == (9, 65, 132, 76)


def test_ocr_tsv_parse_ignores_empty_and_negative_conf():
    tsv = "\n".join(
        [
            _TSV_HEADER,
            "5\t1\t1\t1\t1\t1\t0\t0\t0\t0\t-1\t",
            "5\t1\t1\t1\t1\t1\t10\t10\t5\t5\t-1\tnoise",
            "5\t1\t1\t1\t1\t2\t20\t20\t5\t5\t90\tok",
        ]
    )
    regions = TesseractOCREngine._parse_tsv(tsv)
    assert [r.text for r in regions] == ["ok", "ok"]


def test_ocr_tsv_parse_maps_upscaled_boxes_back_to_frame_pixels():
    """Tesseract runs on the 3x image; boxes must be divided back down so callers
    click original frame coordinates, not enlarged ones."""
    tsv = "\n".join(
        [
            _TSV_HEADER,
            "5\t1\t1\t1\t1\t1\t27\t195\t66\t33\t86\tFile",
        ]
    )
    regions = TesseractOCREngine._parse_tsv(tsv, scale=3)
    by_text = {r.text: r for r in regions}
    assert by_text["File"].bounding_box == (9, 65, 31, 76)


def test_ocr_normalize_folds_case_and_separators():
    assert TesseractOCREngine._normalize("GUI-OK") == "guiok"
    assert TesseractOCREngine._normalize("GUI-K|") == "guik"
    assert TesseractOCREngine._normalize("GUI - OK") == "guiok"


def test_ocr_reads_marker_misread_at_one_scale_by_unioning_scales(tmp_path, monkeypatch):
    """A marker tesseract misreads at one upscale must still be found.

    Reproduces the measured real-desktop defect: at 3x upscale tesseract reads
    the trailing zero of ``NEURO12A-GUI-010`` as ``6`` (a confident-but-wrong
    reading), so the verifier fails closed on correct work. The stub binary
    misreads at 3x and reads correctly at 2x; the engine must union both scales
    and report the marker.
    """
    import sys

    import numpy as np
    from neural.gui_intelligence.models import ScreenFrame

    stub = tmp_path / "tesseract"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from PIL import Image\n"
        "img = Image.open(sys.argv[1])\n"
        "scale = img.width // 100  # frame is 100px wide; tesseract ran on width*scale\n"
        "text = 'NEURO12A-GUI-010' if scale == 2 else 'NEURO12A-GUI-016'\n"
        "print('level\\tpage_num\\tblock_num\\tpar_num\\tline_num\\tword_num\\t"
        "left\\ttop\\twidth\\theight\\tconf\\ttext')\n"
        "print(f'5\\t1\\t1\\t1\\t1\\t1\\t0\\t0\\t{40*scale}\\t{10*scale}\\t90\\t{text}')\n"
    )
    stub.chmod(0o755)

    engine = TesseractOCREngine(tesseract_bin=str(stub))
    pixels = np.zeros((40, 100, 3), dtype=np.uint8)
    frame = ScreenFrame(
        timestamp=0.0, resolution=(100, 40), pixels=pixels, frame_hash="h1"
    )
    matches = engine.find_text(frame, "NEURO12A-GUI-010", fuzzy=True, min_confidence=0.7)
    assert matches, "unioning scales must recover the marker misread at 3x"

    # A frame already read is served from cache: one OCR pass per frame.
    calls = {"n": 0}
    original = engine._run_tsv

    def counting(frame, upscale):
        calls["n"] += 1
        return original(frame, upscale)

    monkeypatch.setattr(engine, "_run_tsv", counting)
    engine.extract_text_regions(frame)
    assert calls["n"] == 0, "a repeated frame must not re-run tesseract"


def test_ocr_does_not_fuzzy_match_a_different_marker_number(tmp_path):
    """A wrong marker on screen must not satisfy a query for a different marker.

    Marker queries share the ``NEURO12A-GUI-`` prefix, so the fuzzy ratio against
    ``...-039`` stays high for a query of ``...-100``. Without a digit guard the
    verifier would accept the wrong document.
    """
    import sys

    import numpy as np
    from neural.gui_intelligence.models import ScreenFrame

    stub = tmp_path / "tesseract"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "print('level\\tpage_num\\tblock_num\\tpar_num\\tline_num\\tword_num\\t"
        "left\\ttop\\twidth\\theight\\tconf\\ttext')\n"
        "print('5\\t1\\t1\\t1\\t1\\t1\\t0\\t0\\t120\\t20\\t92\\tNEURO12A-GUI-039')\n"
    )
    stub.chmod(0o755)

    engine = TesseractOCREngine(tesseract_bin=str(stub), upscales=(2,))
    pixels = np.zeros((40, 100, 3), dtype=np.uint8)
    frame = ScreenFrame(timestamp=0.0, resolution=(100, 40), pixels=pixels, frame_hash="h2")
    assert engine.find_text(frame, "NEURO12A-GUI-100", fuzzy=True, min_confidence=0.7) == []
    assert engine.find_text(frame, "NEURO12A-GUI-039", fuzzy=True, min_confidence=0.7)


def test_engine_refuses_blind_click_when_grounding_returns_negative():
    from neural.gui_intelligence.worker import GuiStepPlan, GuiWorkerEngine

    class _BlindGrounder:
        def ground_query(self, frame, target):
            return type(
                "T", (), {"coordinates": (-1, -1), "confidence": 0.1, "is_confident": False}
            )()

    engine = GuiWorkerEngine(container_manager=object(), require_real_desktop=False)
    from neural.gui_intelligence.worker import GuiWorkerExecutionError

    with pytest.raises(GuiWorkerExecutionError):
        engine._to_request(
            "ANC-EMP-TEST",
            GuiStepPlan(action_type="MOUSE_CLICK", target="Missing"),
            frame=None,
            grounder=_BlindGrounder(),
        )


def test_canonicalize_hotkey_restores_x_keysym_case():
    """xdotool silently drops unknown keysym names: lowercasing 'Return'/'End'
    produces "No such key name 'return'" and the keypress is lost, which broke
    real-desktop file-dialog and text-entry flows."""
    assert canonicalize_hotkey("return") == "Return"
    assert canonicalize_hotkey("Return") == "Return"
    assert canonicalize_hotkey("ctrl+end") == "ctrl+End"
    assert canonicalize_hotkey("Ctrl+Shift+Tab") == "ctrl+shift+Tab"
    assert canonicalize_hotkey("ctrl+o") == "ctrl+o"
    assert canonicalize_hotkey("Escape") == "Escape"
    assert canonicalize_hotkey("page_down") == "Page_Down"


def test_keyboard_controller_sends_canonical_hotkey_to_dispatch():
    from neural.gui_intelligence.action.keyboard_controller import KeyboardController

    seen: list[tuple[str, dict]] = []
    controller = KeyboardController(dispatch_fn=lambda event, payload: seen.append((event, payload)))
    controller.send_hotkey("return")
    controller.send_hotkey("ctrl+end")
    assert seen[0] == ("KEY_HOTKEY", {"hotkey": "Return"})
    assert seen[1] == ("KEY_HOTKEY", {"hotkey": "ctrl+End"})


def test_planner_plans_document_edit_choreography():
    """An edit objective for a named document must plan open→edit→save, not a
    single click, and must type the absolute workspace path into the file dialog."""
    steps = derive_gui_steps(
        "Edit the handoff document on the desktop",
        "Stage 2 (graphical desktop only): open handoff.txt in the text editor via the "
        "graphical desktop and add the line GUI_EDIT=HYBRID_GUI_OK, then save the document "
        "using the GUI.",
    )
    assert [s["target"] for s in steps[:3]] == ["Applications", "Accessories", "Mousepad"]
    assert {"action_type": "KEY_HOTKEY", "key_code": "ctrl+o", "confidence": 0.95} in steps
    assert {"action_type": "KEY_HOTKEY", "key_code": "ctrl+l", "confidence": 0.95} in steps
    assert {
        "action_type": "KEY_TYPE",
        "text": "/home/employee/workspace/handoff.txt",
        "confidence": 0.95,
        "type_into_active_focus": True,
    } in steps
    assert {"action_type": "KEY_HOTKEY", "key_code": "ctrl+s", "confidence": 0.95} in steps
    assert {
        "action_type": "KEY_TYPE",
        "text": "GUI_EDIT=HYBRID_GUI_OK",
        "confidence": 0.95,
        "type_into_active_focus": True,
    } in steps
    assert expected_onscreen_text(steps) == "GUI_EDIT=HYBRID_GUI_OK"


def test_planner_bare_open_stays_a_click_task():
    """A named file without an edit verb must not become an edit choreography."""
    steps = derive_gui_steps("Open handoff.txt", "Open handoff.txt in the file manager")
    assert steps == [
        {"action_type": "MOUSE_CLICK", "target": "Applications", "confidence": 0.95},
        {"action_type": "MOUSE_CLICK", "target": "File Manager", "confidence": 0.95},
    ]


def test_engine_does_not_reuse_click_coordinate_for_active_focus_typing():
    """Typing into a dialog's active focus must not be preceded by a click at the
    last cursor position; that click steals focus back from the dialog."""
    from neural.gui_intelligence.worker import GuiStepPlan, GuiWorkerEngine

    engine = GuiWorkerEngine(container_manager=object(), require_real_desktop=False)
    request = engine._to_request(
        "ANC-EMP-TEST",
        GuiStepPlan(action_type="KEY_TYPE", text="/tmp/x", type_into_active_focus=True),
        frame=None,
        grounder=None,
        fallback_coordinates=(320, 240),
    )
    assert request.coordinates is None
    assert request.active_focus is True
    assert request.text_content == "/tmp/x"


def test_planner_keeps_hybrid_document_edit_as_gui_choreography():
    """A hybrid task names a terminal phase too; that mention must not disable the
    GUI edit choreography, and the payload typed must be the GUI-phase one."""
    steps = derive_gui_steps(
        "Create handoff.txt with terminal lines",
        "Create workspace file handoff.txt with exact content",
        "terminal: create handoff.txt with ANCOS_HYBRID_TEST and TERMINAL_PREPARED=1; "
        "then open in GUI editor, add GUI_EDIT=HYBRID_GUI_OK, save; "
        "then terminal: read and print SHA-256 hash",
    )
    typed = [step["text"] for step in steps if step["action_type"] == "KEY_TYPE"]
    assert "/home/employee/workspace/handoff.txt" in typed
    assert "GUI_EDIT=HYBRID_GUI_OK" in typed
    assert "TERMINAL_PREPARED=1" not in typed
    assert expected_onscreen_text(steps) == "GUI_EDIT=HYBRID_GUI_OK"


def test_planner_rejects_document_edit_without_gui_signal():
    """A pure-terminal document write must not become a GUI choreography."""
    steps = derive_gui_steps(
        "Append a line to notes.txt",
        "Append the line DONE to notes.txt using the shell",
        "terminal only",
    )
    assert all(step["action_type"] != "KEY_TYPE" for step in steps)


def test_planner_settle_steps_are_configurable():
    """The editor handoff must allow a real settle window, not a fixed 50 ms."""
    steps = derive_gui_steps(
        "Edit handoff.txt via GUI",
        "Open handoff.txt in the graphical text editor and add the line GUI_EDIT=HYBRID_GUI_OK, then save",
    )
    settles = [step for step in steps if step["action_type"] == "WAIT_SETTLE"]
    assert settles and all(step.get("settle_ms", 0) >= 300 for step in settles)


def test_planner_settles_after_launch_before_the_first_keystroke():
    """Ctrl+O is delivered before the freshly launched editor window exists and is
    silently dropped; a settle must separate the Mousepad click from Ctrl+O."""
    steps = derive_gui_steps(
        "Edit handoff.txt via GUI",
        "Open handoff.txt in the graphical text editor and add the line GUI_EDIT=HYBRID_GUI_OK, then save",
    )
    launch_index = next(i for i, s in enumerate(steps) if s.get("target") == "Mousepad")
    ctrl_o_index = next(i for i, s in enumerate(steps) if s.get("key_code") == "ctrl+o")
    between = steps[launch_index + 1 : ctrl_o_index]
    assert any(s["action_type"] == "WAIT_SETTLE" and s.get("settle_ms", 0) >= 1500 for s in between), (
        "the editor window needs a settle before Ctrl+O is sent"
    )


def test_planner_does_not_press_return_before_the_appended_payload():
    """Ctrl+End already lands on the empty final line; a Return before the payload
    inserts a *blank* line, yielding an extra empty element in the artifact."""
    steps = derive_gui_steps(
        "Edit handoff.txt via GUI",
        "Open handoff.txt in the graphical text editor and add the line GUI_EDIT=HYBRID_GUI_OK, then save",
    )
    ctrl_end_index = next(i for i, s in enumerate(steps) if s.get("key_code") == "ctrl+End")
    payload_index = next(
        i
        for i, s in enumerate(steps)
        if s["action_type"] == "KEY_TYPE" and s.get("text") == "GUI_EDIT=HYBRID_GUI_OK"
    )
    between = steps[ctrl_end_index + 1 : payload_index]
    assert not any(s.get("key_code") in {"Return", "KP_Enter"} for s in between)


def test_planner_recovers_paraphrased_hybrid_payload_from_acceptance_criteria():
    """The Boss planner paraphrases free-form objectives and can drop the literal
    graphical payload, keeping it only in the acceptance criteria. The planner
    must still build the open→edit→save choreography from that authoritative
    contract instead of degrading to a few clicks with nothing typed."""
    steps = derive_gui_steps(
        "Create handoff.txt and perform hybrid GUI edit",
        "Stage 1: Create handoff.txt with two lines. Stage 2: Open handoff.txt in GUI "
        "text editor and add GUI_EDIT line. Stage 3: Read file and print SHA-256 hash.",
        "",
        "handoff.txt exists with exactly three lines: ANCOS_HYBRID_TEST, "
        "TERMINAL_PREPARED=1, GUI_EDIT=HYBRID_GUI_OK; SHA-256 hash is printed and "
        "matches the file content",
    )
    typed = [step["text"] for step in steps if step["action_type"] == "KEY_TYPE"]
    assert "/home/employee/workspace/handoff.txt" in typed
    assert "GUI_EDIT=HYBRID_GUI_OK" in typed
    # The terminal-phase payload belongs to prepare, not to the graphical phase.
    assert "TERMINAL_PREPARED=1" not in typed
    assert expected_onscreen_text(steps) == "GUI_EDIT=HYBRID_GUI_OK"


def test_planner_saves_the_document_edit_even_when_the_save_verb_is_paraphrased():
    """An unsaved edit fails the verify phase, so a document-edit choreography
    must persist unconditionally, not only when the objective still says "save"."""
    steps = derive_gui_steps(
        "Edit handoff.txt on the desktop",
        "Open handoff.txt in the graphical editor and add the line GUI_EDIT=HYBRID_GUI_OK",
        "",
        "handoff.txt contains the line GUI_EDIT=HYBRID_GUI_OK",
    )
    assert {"action_type": "KEY_HOTKEY", "key_code": "ctrl+s", "confidence": 0.95} in steps


def test_planner_prefers_gui_named_payload_over_terminal_named_one():
    """A hybrid title ("... hybrid terminal+GUI workflow") puts a GUI hint within
    the proximity window of the terminal-side payload too, so a proximity-first
    selector typed TERMINAL_PREPARED=1 on the desktop and the GUI edit never
    happened. The key name (GUI_EDIT=...) must outrank proximity."""
    steps = derive_gui_steps(
        "Create and edit handoff.txt via hybrid terminal+GUI workflow",
        "Stage 1: Create handoff.txt with ANCOS_HYBRID_TEST and TERMINAL_PREPARED=1 "
        "using terminal. Stage 2: Open handoff.txt in GUI text editor and add "
        "GUI_EDIT=HYBRID_GUI_OK, save. Stage 3: Read handoff.txt in terminal and "
        "print SHA-256 hash.",
        "Create handoff.txt, edit via GUI, verify hash via terminal",
        "handoff.txt contains exactly three lines: ANCOS_HYBRID_TEST, "
        "TERMINAL_PREPARED=1, GUI_EDIT=HYBRID_GUI_OK. Terminal prints correct "
        "SHA-256 hash of the file content.",
    )
    typed = [step["text"] for step in steps if step["action_type"] == "KEY_TYPE"]
    assert "GUI_EDIT=HYBRID_GUI_OK" in typed
    assert "TERMINAL_PREPARED=1" not in typed
    assert expected_onscreen_text(steps) == "GUI_EDIT=HYBRID_GUI_OK"


# ---------------------------------------------------------------------------
# GUI save reliability (NEURO-12D): Ctrl+S is not proof of a save.
# ---------------------------------------------------------------------------


def test_planner_confirms_the_save_from_app_state_after_ctrl_s():
    """A document edit must end with a CONFIRM_SAVED step, not just Ctrl+S.

    Ctrl+S only proves a keystroke was sent. If the save is dropped (lost focus,
    a modal dialog) the file is never written, yet every step still reports
    success and the verify phase fails with no explanation. The plan must confirm
    the save from the editor's own state, keyed on the document's file name.
    """
    steps = derive_gui_steps(
        "Edit handoff.txt via GUI",
        "Open handoff.txt in the graphical text editor and add the line "
        "GUI_EDIT=HYBRID_GUI_OK, then save",
    )
    assert steps[-1]["action_type"] == "CONFIRM_SAVED"
    assert steps[-1]["expected_text"] == "handoff.txt"
    # The confirmation must follow the save keystroke, not replace it.
    ctrl_s = next(i for i, s in enumerate(steps) if s.get("key_code") == "ctrl+s")
    assert ctrl_s < len(steps) - 1


def _make_loop(window_state_provider):
    from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop

    return GUIExecutionLoop(window_state_provider=window_state_provider)


def test_confirm_saved_succeeds_when_document_is_unmodified():
    loop = _make_loop(lambda: ["~/workspace/handoff.txt - Mousepad"])
    receipt = loop.execute_action(
        GUIActionRequest(
            action_id="act_saved",
            employee_id="emp",
            action_type="CONFIRM_SAVED",
            confidence=0.95,
            expected_text="handoff.txt",
            settle_ms=200,
        )
    )
    assert receipt.status == "SUCCESS"


def test_confirm_saved_fails_closed_when_document_is_still_dirty():
    """A document still carrying the '*' modified marker must fail the save step.

    The window title names the file but is still prefixed with '*', so the editor
    itself is reporting the edit as unsaved; the confirmation must not accept it.
    """
    loop = _make_loop(lambda: ["*~/workspace/handoff.txt - Mousepad"])
    receipt = loop.execute_action(
        GUIActionRequest(
            action_id="act_dirty",
            employee_id="emp",
            action_type="CONFIRM_SAVED",
            confidence=0.95,
            expected_text="handoff.txt",
            settle_ms=150,
        )
    )
    assert receipt.status == "BLOCKED_BY_SAFETY"
    assert "Save not confirmed" in (receipt.error_message or "")


def test_confirm_saved_reissues_ctrl_s_when_document_remains_dirty():
    """A dropped save keystroke must be retried, not failed immediately.

    The target document is open but still dirty, so the loop re-issues Ctrl+S
    and succeeds once the app reports it saved. A redundant Ctrl+S is harmless.
    """
    from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop

    states = iter(
        [
            ["*~/workspace/handoff.txt - Mousepad"],
            ["~/workspace/handoff.txt - Mousepad"],
        ]
    )
    sent: list[str] = []

    class _Keyboard:
        def send_hotkey(self, hotkey: str) -> float:
            sent.append(hotkey)
            return 0.0

    loop = GUIExecutionLoop(
        window_state_provider=lambda: next(states), keyboard_controller=_Keyboard()
    )
    receipt = loop.execute_action(
        GUIActionRequest(
            action_id="act_retry",
            employee_id="emp",
            action_type="CONFIRM_SAVED",
            confidence=0.95,
            expected_text="handoff.txt",
            settle_ms=4000,
        )
    )
    assert receipt.status == "SUCCESS"
    assert sent == ["ctrl+s"]


def test_confirm_saved_requires_a_window_state_provider():
    """Without the live window state the confirmation cannot be honest, so it must
    fail closed rather than assume the save worked."""
    from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop

    loop = GUIExecutionLoop()
    receipt = loop.execute_action(
        GUIActionRequest(
            action_id="act_noprovider",
            employee_id="emp",
            action_type="CONFIRM_SAVED",
            confidence=0.95,
            expected_text="handoff.txt",
            settle_ms=150,
        )
    )
    assert receipt.status == "BLOCKED_BY_SAFETY"
    assert "window-state provider" in (receipt.error_message or "")


def test_document_saved_ignores_dirty_marker_and_foreign_documents():
    from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop

    dirty = ["*~/workspace/handoff.txt - Mousepad"]
    foreign = ["~/workspace/other.txt - Mousepad"]
    saved = ["~/workspace/handoff.txt - Mousepad"]
    assert GUIExecutionLoop._document_saved(dirty, "handoff.txt") is False
    assert GUIExecutionLoop._document_saved(foreign, "handoff.txt") is False
    assert GUIExecutionLoop._document_saved(saved, "handoff.txt") is True
    # Without a marker, a titled document window still counts, but a bare app
    # window (no document) must not vouch for a save.
    assert GUIExecutionLoop._document_saved(["Mousepad"], None) is False
    assert GUIExecutionLoop._document_saved(["~/workspace/x - Mousepad"], None) is True


def test_driver_window_titles_reads_visible_titles():
    manager = _RecordingManager(stdout="Mousepad\n*~/workspace/handoff.txt - Mousepad\n")
    driver = ContainerDesktopDriver(manager, "ancos-desktop-anc-emp-test", display=":1")
    titles = driver.window_titles()
    assert titles == ["Mousepad", "*~/workspace/handoff.txt - Mousepad"]
    shell = manager.commands[-1][-1]
    assert "xdotool search" in shell and "getwindowname" in shell


class _ScreenshotManager:
    """Serves real PNGs for scrot captures and success for every motor command.

    Successive captures alternate between two colours so the final-frame delta is
    a genuine visual change, exactly as a real desktop would report after an action.
    """

    def __init__(self) -> None:
        import base64
        import io

        from PIL import Image

        self._pngs: list[str] = []
        for colour in ((20, 40, 60), (200, 220, 240)):
            buffer = io.BytesIO()
            Image.new("RGB", (128, 80), colour).save(buffer, format="PNG")
            self._pngs.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
        self._captures = 0

    def execute_command_sync(
        self, docker_id: str, command: list[str], workdir: str | None = None
    ) -> dict[str, Any]:
        script = command[-1] if command else ""
        if "scrot" in script:
            png = self._pngs[min(self._captures, len(self._pngs) - 1)]
            self._captures += 1
            return {"exit_code": 0, "stdout": png, "stderr": ""}
        if "getwindowname" in script:
            return {"exit_code": 0, "stdout": "Mousepad\n", "stderr": ""}
        return {"exit_code": 0, "stdout": "", "stderr": ""}


def test_worker_result_reports_phase_latency_attribution():
    """The whole-run result must attribute wall-clock time to measured phases.

    Stage 1 latency instrumentation: without this, "how slow is the GUI loop and
    where does the time go" is a guess. The run must report grounding, per-step
    execution, verification and total time, each finite and non-negative, and the
    parts must not exceed the whole.
    """
    from neural.gui_intelligence.worker import GuiStepPlan, GuiWorkerEngine

    engine = GuiWorkerEngine(container_manager=_ScreenshotManager(), require_real_desktop=False)
    result = engine.execute(
        employee_id="ANC-EMP-TEST",
        docker_id="ancos-desktop-anc-emp-test",
        steps=[GuiStepPlan(action_type="MOUSE_MOVE", coordinates=(60, 40))],
    )

    assert result.status == "success", result.error
    assert result.steps_executed == 1
    for key in ("grounding_ms", "step_ms", "verification_ms", "run_ms"):
        assert key in result.timings, f"missing measured phase {key}"
        assert result.timings[key] >= 0.0
        assert result.timings[key] != float("inf")
    phases = result.timings["grounding_ms"] + result.timings["step_ms"]
    assert phases <= result.timings["run_ms"] + 1.0
    # The phase breakdown must survive serialisation to the API response.
    assert result.to_dict()["timings"] == result.timings

