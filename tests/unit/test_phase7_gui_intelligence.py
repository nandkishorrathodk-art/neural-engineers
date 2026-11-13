from __future__ import annotations

import numpy as np
import pytest
from neural.exceptions import (
    EmergencyStopTriggered,
    FocusVerificationFailedError,
    HumanApprovalRequiredError,
    StrictGUIContractViolation,
    VisualUncertaintyError,
)
from neural.gui_intelligence.action.action_batcher import ActionBatcher
from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop
from neural.gui_intelligence.action.keyboard_controller import KeyboardController
from neural.gui_intelligence.action.mouse_controller import MouseController
from neural.gui_intelligence.benchmarks.human_speed import HumanSpeedComparator
from neural.gui_intelligence.benchmarks.performance_report import PerformanceReporter
from neural.gui_intelligence.benchmarks.success_metrics import GUISuccessMetrics
from neural.gui_intelligence.brain.cognitive_controller import DualBrainCognitiveController
from neural.gui_intelligence.brain.fast_policy import System1FastPolicy
from neural.gui_intelligence.brain.task_planner import (
    CompiledExecutionPlan,
    PlanStep,
    System2TaskPlanner,
)
from neural.gui_intelligence.models import (
    GUIActionReceipt,
    GUIActionRequest,
    ScreenFrame,
    VisualElement,
)
from neural.gui_intelligence.perception.object_detection import UIObjectDetector
from neural.gui_intelligence.perception.pixel_ocr import PixelOCREngine
from neural.gui_intelligence.perception.screen_capture import ScreenCaptureEngine
from neural.gui_intelligence.perception.screen_change import ScreenChangeDetector
from neural.gui_intelligence.perception.visual_encoder import PyramidalVisualEncoder
from neural.gui_intelligence.perception.visual_grounding import VisualGrounder
from neural.gui_intelligence.recovery.failure_detector import GUIFailureDetector
from neural.gui_intelligence.recovery.state_recovery import GUIStateRecovery
from neural.gui_intelligence.recovery.visibility_recovery import VisibilityRecoveryEngine
from neural.gui_intelligence.safety.gui_action_policy import GUIActionPolicy
from neural.gui_intelligence.safety.risk_guard import GUIRiskGuard
from neural.gui_intelligence.skills.experience_recorder import ExperienceRecorder
from neural.gui_intelligence.skills.skill_compiler import GUISkillCompiler
from neural.gui_intelligence.skills.skill_registry import GUISkillRegistry
from neural.gui_intelligence.skills.skill_validator import SkillValidator
from neural.gui_intelligence.verification.task_completion import TaskCompletionCertificateGenerator
from neural.gui_intelligence.verification.visual_verifier import (
    IndependentVisualVerifier,
    VisualVerificationResult,
)
from neural.gui_intelligence.world_model.screen_state import ScreenStateManager
from neural.gui_intelligence.world_model.transition_tracker import VisualTransitionTracker
from neural.gui_intelligence.world_model.visual_belief import VisualBeliefStateManager
from neural.gui_intelligence.world_model.window_state import WindowStateTracker
from neural.security.kill_switch import GlobalEmergencyKillSwitch

# ---------------------------------------------------------------------------
# 1. Perception Tests
# ---------------------------------------------------------------------------


def test_screen_capture_engine_synthetic_and_latency():
    """Validates ScreenCaptureEngine renders synthetic desktop in <15ms with valid sha256."""
    engine = ScreenCaptureEngine(default_resolution=(1280, 800))
    frame = engine.capture_frame()

    assert frame.resolution == (1280, 800)
    assert frame.width == 1280
    assert frame.height == 800
    assert frame.pixels.shape == (800, 1280, 3)
    assert len(frame.frame_hash) == 64
    assert frame.capture_latency_ms < 15.0


def test_screen_capture_mock_frame():
    """Validates set_mock_frame injection for deterministic testing."""
    engine = ScreenCaptureEngine()
    test_pixels = np.zeros((400, 600, 3), dtype=np.uint8)
    test_pixels[:, :, 0] = 255  # Red screen

    engine.set_mock_frame(test_pixels)
    frame = engine.capture_frame()

    assert frame.resolution == (600, 400)
    assert frame.pixels[0, 0, 0] == 255
    assert frame.pixels[0, 0, 1] == 0


def test_pyramidal_visual_encoder():
    """Validates Dual-Fovea Pyramidal Visual Encoder (Section 5.1)."""
    engine = ScreenCaptureEngine()
    frame = engine.render_synthetic_desktop()
    encoder = PyramidalVisualEncoder(coarse_resolution=(768, 432), foveal_size=(384, 384))
    _ = encoder.encode(frame)  # warm-up JIT and C extension dispatch

    sample = encoder.encode(frame, focus_point=(640, 400))

    assert sample.global_view.shape == (432, 768, 3)
    assert sample.foveal_patch.shape == (384, 384, 3)
    assert sample.focus_point == (640, 400)
    assert len(sample.foveal_signature) == 16
    assert sample.encoding_latency_ms < 50.0


def test_pixel_ocr_engine():
    """Validates Pixel-Only OCR Engine operates over pixels and reads titles."""
    engine = ScreenCaptureEngine()
    frame = engine.render_synthetic_desktop(
        windows=[
            {
                "title": "Document Editor - Unsaved",
                "rect": (100, 80, 700, 450),
                "is_active": True,
            }
        ]
    )
    ocr = PixelOCREngine()
    ocr.register_mock_text("Save As...", (200, 150, 280, 180), confidence=0.96)

    # Search for registered text
    matches = ocr.find_text(frame, "Save As...")
    assert len(matches) == 1
    assert matches[0].text == "Save As..."
    assert matches[0].confidence >= 0.95

    # Visual Title bar reader
    ocr.register_mock_text("Document Editor - Unsaved", (110, 85, 300, 115), confidence=0.98)
    title = ocr.read_title_bar(frame, (100, 80, 700, 450))
    assert "Document Editor" in title


def test_ui_object_detector():
    """Validates UIObjectDetector identifies bounding boxes and focus rings."""
    engine = ScreenCaptureEngine()
    frame = engine.render_synthetic_desktop(
        windows=[
            {
                "title": "App",
                "rect": (100, 100, 600, 400),
                "elements": [
                    {"type": "button", "text": "OK", "rect": (120, 150, 80, 32)},
                    {
                        "type": "text_input",
                        "text": "name",
                        "rect": (120, 200, 150, 32),
                        "is_focused": True,
                    },
                ],
            }
        ]
    )
    detector = UIObjectDetector()
    detector.register_mock_element(
        VisualElement(
            element_id="btn_ok",
            bounding_box=(120, 150, 200, 182),
            element_type="button",
            visual_text="OK",
            visual_signature="sig_ok",
            confidence=0.95,
            is_interactive=True,
            is_focused=False,
        )
    )
    elements = detector.detect(frame)
    assert len(elements) >= 1
    btn = next(e for e in elements if e.element_id == "btn_ok")
    assert btn.element_type == "button"
    assert btn.center() == (160, 166)


def test_visual_grounder_confidence_and_rejection():
    """Validates VisualGrounder grounds queries and enforces confidence threshold."""
    engine = ScreenCaptureEngine()
    frame = engine.render_synthetic_desktop()
    ocr = PixelOCREngine()
    ocr.register_mock_text("Submit Order", (400, 300, 520, 340), confidence=0.94)

    grounder = VisualGrounder(ocr_engine=ocr)
    target = grounder.ground_query(frame, "Submit Order", require_confidence=True)

    assert target.coordinates == (460, 320)
    assert target.confidence >= 0.88
    assert target.is_confident

    # Unconfident query should raise VisualUncertaintyError when required
    with pytest.raises(VisualUncertaintyError):
        grounder.ground_query(frame, "NonexistentUnknownElementXYZ", require_confidence=True)


def test_screen_change_detector_settle():
    """Validates ScreenChangeDetector delta computation and settle detection."""
    engine = ScreenCaptureEngine()
    frame1 = engine.render_synthetic_desktop()
    frame2 = engine.render_synthetic_desktop()  # identical frame

    detector = ScreenChangeDetector()
    delta = detector.compute_delta(frame1, frame2)

    assert delta.magnitude == 0.0
    assert delta.is_settled
    assert detector.is_settled([frame1, frame2])

    # Alter frame2 significantly
    altered_pixels = frame1.pixels.copy()
    altered_pixels[100:300, 100:300, :] = 255
    altered_frame = ScreenFrame(
        timestamp=frame1.timestamp + 0.1,
        resolution=frame1.resolution,
        pixels=altered_pixels,
        frame_hash="alt_hash",
    )
    diff_delta = detector.compute_delta(frame1, altered_frame)
    assert diff_delta.magnitude > 0.01
    assert not diff_delta.is_settled


# ---------------------------------------------------------------------------
# 2. World Model Tests
# ---------------------------------------------------------------------------


def test_screen_state_manager_queries():
    """Validates ScreenStateManager spatial queries without querying OS APIs."""
    mgr = ScreenStateManager()
    engine = ScreenCaptureEngine()
    frame = engine.capture_frame()

    elem1 = VisualElement("btn_1", (100, 100, 200, 150), "button", "Submit", "sig1", 0.9, True)
    elem2 = VisualElement(
        "input_1", (100, 200, 300, 240), "text_input", "Username", "sig2", 0.95, True
    )

    mgr.update_state(frame, [elem1, elem2])

    assert mgr.element_at(150, 120) == elem1
    assert mgr.element_at(150, 220) == elem2
    assert mgr.element_at(50, 50) is None

    assert len(mgr.find_by_type("button")) == 1
    assert len(mgr.find_by_text("user")) == 1
    assert mgr.find_closest(110, 190, "text_input") == elem2


def test_window_state_tracker_z_order_and_dialogs():
    """Validates WindowStateTracker z-order and modal dialog detection."""
    tracker = WindowStateTracker()

    tracker.register_window("win_main", (50, 50, 800, 600), "Main Window", is_active=True)
    assert tracker.active_window.window_id == "win_main"

    dialog = VisualElement(
        "dlg_elem", (300, 200, 600, 400), "dialog", "Confirm Action", "sig_d", 0.99, True
    )
    tracker.register_window(
        "win_dialog", (300, 200, 600, 400), "Confirm Action", is_active=True, elements=[dialog]
    )

    assert tracker.active_window.window_id == "win_dialog"
    assert len(tracker.detect_active_dialogs()) == 1

    # Bring main window back to front
    tracker.bring_to_front("win_main")
    assert tracker.active_window.window_id == "win_main"


def test_visual_belief_state_uncertainty_escalation():
    """Validates VisualBeliefStateManager compounds uncertainty and triggers escalation."""
    mgr = VisualBeliefStateManager()
    engine = ScreenCaptureEngine()
    frame = engine.capture_frame()

    # Baseline nominal state
    state = mgr.update_belief(frame, [], [], grounding_uncertainty=0.03)
    assert state.uncertainty_score < 0.12
    assert not mgr.requires_escalation

    # When modal dialog appears, uncertainty spikes
    dialog_elem = VisualElement("d1", (100, 100, 400, 300), "dialog", "Alert", "sig", 0.9, True)
    state_popup = mgr.update_belief(frame, [], [dialog_elem], grounding_uncertainty=0.05)
    assert state_popup.uncertainty_score >= 0.45
    assert mgr.requires_escalation


def test_visual_transition_tracker():
    """Validates VisualTransitionTracker confirms nominal transitions and flags stalls."""
    tracker = VisualTransitionTracker()
    engine = ScreenCaptureEngine()
    frame1 = engine.capture_frame()
    frame2 = engine.capture_frame()

    req = GUIActionRequest(
        action_id="act_click",
        employee_id="emp_test",
        action_type="MOUSE_CLICK",
        coordinates=(300, 300),
        confidence=0.95,
    )
    tracker.register_expected_transition(req, "Button visual highlight")

    # Identical frame on click triggers stall result
    res_stalled = tracker.verify_transition(frame1, frame2)
    assert not res_stalled.success
    assert res_stalled.uncertainty >= 0.85

    # Move action should pass even without delta
    req_move = GUIActionRequest(
        action_id="act_move",
        employee_id="emp_test",
        action_type="MOUSE_MOVE",
        coordinates=(400, 400),
        confidence=0.99,
    )
    tracker.register_expected_transition(req_move, "Move cursor")
    res_move = tracker.verify_transition(frame1, frame2)
    assert res_move.success


# ---------------------------------------------------------------------------
# 3. Action Execution & Motor Tests
# ---------------------------------------------------------------------------


def test_mouse_controller_smooth_cosine_waypoints():
    """Validates MouseController generates smooth cosine-eased trajectory waypoints (Section 9.1)."""
    mouse = MouseController(initial_position=(100, 100))
    waypoints = mouse.compute_trajectory_waypoints(
        (100, 100), (500, 400), duration_ms=220.0, num_waypoints=10
    )

    assert len(waypoints) == 11
    assert waypoints[0] == (100, 100, 0.0)
    assert waypoints[-1] == (500, 400, 220.0)

    # Check midpoint easing: around step 5 (halfway), x should be approx midpoint 300
    mid_x, mid_y, _ = waypoints[5]
    assert abs(mid_x - 300) <= 5
    assert abs(mid_y - 250) <= 5


def test_mouse_controller_actions():
    """Validates move, click, double click, drag, scroll tracking."""
    dispatched = []
    mouse = MouseController(dispatch_fn=lambda t, p: dispatched.append((t, p)))

    mouse.click(200, 150)
    assert mouse.cursor_position == (200, 150)
    assert any(d[0] == "MOUSE_CLICK" for d in dispatched)

    mouse.drag(200, 150, 400, 300)
    assert mouse.cursor_position == (400, 300)
    assert any(d[0] == "MOUSE_DOWN" for d in dispatched)
    assert any(d[0] == "MOUSE_UP" for d in dispatched)


def test_keyboard_controller_focus_verification_gate():
    """Validates Section 9.2: Text entry without verified focus cue is strictly blocked."""
    kb = KeyboardController()
    unfocused_elem = VisualElement(
        "txt_1", (100, 100, 300, 140), "text_input", "", "sig", 0.9, True, is_focused=False
    )

    # Unverified focus MUST raise FocusVerificationFailedError
    with pytest.raises(FocusVerificationFailedError):
        kb.type_text("Hello World", target_element=unfocused_elem)

    # Verified focus element proceeds
    focused_elem = VisualElement(
        "txt_2", (100, 100, 300, 140), "text_input", "", "sig", 0.9, True, is_focused=True
    )
    elapsed = kb.type_text("Hello World", target_element=focused_elem)
    assert elapsed >= 0.0
    assert len(kb.keystroke_history) == 1


def test_action_batcher_chunking():
    """Validates ActionBatcher macro chunk synthesis."""
    batcher = ActionBatcher(employee_id="emp_gui_test")
    chunk = batcher.create_click_and_type_chunk(
        target_coords=(300, 200),
        text="test@example.com",
        press_enter=True,
    )

    assert len(chunk) == 4
    assert chunk[0].action_type == "MOUSE_CLICK"
    assert chunk[1].action_type == "WAIT_SETTLE"
    assert chunk[2].action_type == "KEY_TYPE"
    assert chunk[2].text_content == "test@example.com"
    assert chunk[3].action_type == "KEY_HOTKEY"
    assert chunk[0].total_chunk_size == 4


def test_gui_execution_loop_and_receipt_signature():
    """Validates GUIExecutionLoop executes action and produces cryptographic receipt signature."""
    loop = GUIExecutionLoop()
    req = GUIActionRequest(
        action_id="act_sig_test",
        employee_id="emp_test",
        action_type="MOUSE_MOVE",
        coordinates=(450, 350),
        confidence=0.99,
    )
    receipt = loop.execute_action(req)

    assert receipt.status == "SUCCESS"
    assert receipt.action_id == "act_sig_test"
    assert len(receipt.signature) == 64
    assert receipt.execution_time_ms >= 0.0


# ---------------------------------------------------------------------------
# 4. Brain & Cognitive Controller Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system2_task_planner_decomposition():
    """Validates System 2 Deep Brain task decomposition into symbolic PlanSteps."""
    planner = System2TaskPlanner()
    plan = await planner.create_plan("Fill user registration form and submit")

    assert len(plan.steps) >= 2
    assert plan.steps[0].action_intent == "CLICK"
    assert "Username" in plan.steps[0].target_descriptor


def test_system1_fast_policy_chunking():
    """Validates System 1 Fast Brain sub-50ms action chunk selection."""
    grounder = VisualGrounder()
    engine = ScreenCaptureEngine()
    frame = engine.render_synthetic_desktop()

    step = PlanStep(
        step_id="step_1",
        phase_name="Click Button",
        action_intent="CLICK",
        target_descriptor="button[text='Applications']",
    )
    grounder.ocr_engine.register_mock_text("Applications", (16, 776, 80, 796), confidence=0.95)

    policy = System1FastPolicy(grounder=grounder)
    chunk = policy.select_action_chunk(step, frame)

    assert len(chunk) >= 1
    assert chunk[0].action_type == "MOUSE_CLICK"
    assert chunk[0].coordinates == (48, 786)


@pytest.mark.asyncio
async def test_dual_brain_cognitive_controller_execution():
    """Validates DualBrainCognitiveController arbitrates and executes task."""
    controller = DualBrainCognitiveController()
    result = await controller.execute_task("Save document to disk", max_steps=3)

    assert result["success"]
    assert result["deep_brain_calls"] >= 1
    assert result["fast_brain_calls"] >= 1
    assert result["total_actions_dispatched"] >= 1
    assert result["verification_receipts"]
    assert all(receipt["verified"] for receipt in result["verification_receipts"])


class _ControllerTestPlanner(System2TaskPlanner):
    async def create_plan(self, task_objective, belief_state=None):
        return CompiledExecutionPlan(
            plan_id="plan_test",
            task_objective=task_objective,
            steps=[
                PlanStep(
                    step_id="step_test",
                    phase_name="Test visual completion",
                    action_intent="CLICK",
                    target_descriptor="button[text='Save']",
                    expected_postcondition="Save confirmation appears",
                )
            ],
        )


class _MultiStepControllerTestPlanner(_ControllerTestPlanner):
    async def create_plan(self, task_objective, belief_state=None):
        plan = await super().create_plan(task_objective, belief_state)
        plan.steps.append(
            PlanStep(
                step_id="step_test_followup",
                phase_name="Verify saved document",
                action_intent="CLICK",
                target_descriptor="button[text='Done']",
                expected_postcondition="Document appears in saved list",
            )
        )
        return plan


class _ControllerTestFastPolicy:
    employee_id = "emp_gui_test"

    def __init__(self, *, uncertain=False):
        self.uncertain = uncertain

    def select_action_chunk(self, step, frame, belief_state=None):
        if self.uncertain:
            raise VisualUncertaintyError("No confident visual target")
        return [
            GUIActionRequest(
                action_id="act_test",
                employee_id=self.employee_id,
                action_type="MOUSE_CLICK",
                coordinates=(10, 10),
                confidence=0.95,
            )
        ]


class _ControllerTestExecutionLoop:
    def __init__(self):
        self.dispatched = []
        self.mouse_controller = MouseController()
        self.keyboard_controller = KeyboardController()

    def execute_chunk(self, chunk):
        self.dispatched.extend(chunk)
        return [
            GUIActionReceipt(
                action_id=request.action_id,
                employee_id=request.employee_id,
                status="SUCCESS",
                execution_time_ms=1.0,
                screen_settled=True,
                pixel_delta_magnitude=0.1,
                observed_postcondition="action received",
                signature="test-signature",
            )
            for request in chunk
        ]


class _ControllerTestCapture:
    def __init__(self):
        self.frames = [
            ScreenCaptureEngine().render_synthetic_desktop(),
            ScreenCaptureEngine().render_synthetic_desktop(background_color=(41, 44, 52)),
        ]

    def capture_frame(self):
        if len(self.frames) > 1:
            return self.frames.pop(0)
        return self.frames[0]


class _ControllerTestVerifier:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def verify_postcondition(
        self, frame_initial, frame_final, expected_text=None, min_delta_magnitude=0.0001
    ):
        self.calls.append((frame_initial, frame_final, expected_text))
        if self.result is None:
            return None
        return VisualVerificationResult(
            passed=self.result in {True, "mismatched_hash"},
            score=1.0 if self.result in {True, "mismatched_hash"} else 0.0,
            delta_magnitude=0.2,
            postcondition_checked="Visual state transition",
            details="test receipt",
            screenshot_hash=(
                "0" * 64 if self.result == "mismatched_hash" else frame_final.frame_hash
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("verification_result", "expected_success"),
    [(True, True), (False, False), (None, False), ("mismatched_hash", False)],
)
async def test_controller_requires_independent_visual_receipt_per_step(
    verification_result, expected_success
):
    capture = _ControllerTestCapture()
    execution = _ControllerTestExecutionLoop()
    verifier = _ControllerTestVerifier(verification_result)
    controller = DualBrainCognitiveController(
        deep_planner=_ControllerTestPlanner(),
        fast_policy=_ControllerTestFastPolicy(),
        execution_loop=execution,
        capture_engine=capture,
        visual_verifier=verifier,
    )

    result = await controller.execute_task("Save document")

    assert result["success"] is expected_success
    assert result["completed_steps"] == (1 if expected_success else 0)
    assert len(verifier.calls) == 1
    assert result["verification_receipts"][0]["verified"] is expected_success
    assert len(execution.dispatched) == 1


@pytest.mark.asyncio
async def test_controller_does_not_report_success_when_max_steps_truncates_the_plan():
    """Finishing the selected prefix must not certify omitted planned work."""
    capture = _ControllerTestCapture()
    execution = _ControllerTestExecutionLoop()
    controller = DualBrainCognitiveController(
        deep_planner=_MultiStepControllerTestPlanner(),
        fast_policy=_ControllerTestFastPolicy(),
        execution_loop=execution,
        capture_engine=capture,
        visual_verifier=_ControllerTestVerifier(True),
    )

    result = await controller.execute_task("Save document and verify it", max_steps=1)

    assert result["success"] is False
    assert result["plan_truncated"] is True
    assert result["total_steps_in_plan"] == 2
    assert result["steps_attempted"] == 1
    assert result["completed_steps"] == 1
    assert len(result["verification_receipts"]) == 1
    assert len(execution.dispatched) == 1


@pytest.mark.asyncio
async def test_controller_does_not_center_click_when_visual_grounding_is_uncertain():
    capture = _ControllerTestCapture()
    execution = _ControllerTestExecutionLoop()
    verifier = _ControllerTestVerifier(True)
    controller = DualBrainCognitiveController(
        deep_planner=_ControllerTestPlanner(),
        fast_policy=_ControllerTestFastPolicy(uncertain=True),
        execution_loop=execution,
        capture_engine=capture,
        visual_verifier=verifier,
    )

    result = await controller.execute_task("Save document")

    assert result["success"] is False
    assert result["completed_steps"] == 0
    assert execution.dispatched == []
    assert verifier.calls == []


# ---------------------------------------------------------------------------
# 5. Recovery Tests
# ---------------------------------------------------------------------------


def test_failure_detector_modal_and_stalls():
    """Validates GUIFailureDetector diagnoses modal dialogs and stalls."""
    detector = GUIFailureDetector()
    engine = ScreenCaptureEngine()
    frame = engine.capture_frame()

    dialog_elem = VisualElement(
        "dlg1", (200, 200, 500, 400), "dialog", "Error Alert", "sig", 0.98, True
    )
    diag_modal = detector.diagnose(frame, frame, [dialog_elem], "MOUSE_CLICK")

    assert diag_modal.has_failure
    assert diag_modal.failure_type == "BLOCKING_MODAL"
    assert diag_modal.suggested_recovery_step == 4


def test_visibility_recovery_engine_hierarchy():
    """Validates 5-step Visibility Recovery hierarchy (Section 8)."""
    rec_engine = VisibilityRecoveryEngine()

    # Step 1: Occluded background window recovery
    win_back = WindowStateTracker().register_window(
        "win_bg", (100, 100, 500, 400), "Settings Window", is_active=False
    )
    res_step1 = rec_engine.recover_control_visibility(
        target_descriptor="Settings",
        visible_windows=[
            WindowStateTracker().register_window("win_active", (0, 0, 800, 600), "Front Window"),
            win_back,
        ],
        active_dialogs=[],
        scrollable_elements=[],
        menu_expanders=[],
    )
    assert res_step1["step"] == 1
    assert res_step1["strategy"] == "WINDOW_OCCLUSION_RECOVERY"

    # Step 4: Blocking dialog dismissal
    dlg = VisualElement("d", (100, 100, 400, 300), "dialog", "Popup", "s", 0.9, True)
    res_step4 = rec_engine.recover_control_visibility(
        target_descriptor="Other",
        visible_windows=[],
        active_dialogs=[dlg],
        scrollable_elements=[],
        menu_expanders=[],
    )
    assert res_step4["step"] == 4
    assert res_step4["strategy"] == "DISMISS_MODAL_DIALOG"


def test_state_recovery_actions():
    """Validates GUIStateRecovery undo and escape routines."""
    rec = GUIStateRecovery()
    res_undo = rec.undo_mutation()
    assert res_undo["strategy"] == "UNDO_CTRL_Z"

    res_esc = rec.dismiss_modal()
    assert res_esc["strategy"] == "DISMISS_ESCAPE"


# ---------------------------------------------------------------------------
# 6. Verification & Safety Tests
# ---------------------------------------------------------------------------


def test_independent_visual_verifier_zero_decree():
    """Validates IndependentVisualVerifier prevents success-by-decree."""
    verifier = IndependentVisualVerifier()
    engine = ScreenCaptureEngine()
    frame1 = engine.render_synthetic_desktop()
    frame2 = engine.render_synthetic_desktop()

    verifier.ocr_engine.register_mock_text(
        "Build Successful", (300, 200, 450, 230), confidence=0.98
    )

    # Frame2 without expected text must fail
    res_fail = verifier.verify_postcondition(
        frame1, frame2, expected_text="Nonexistent Success Text"
    )
    assert not res_fail.passed

    # Frame2 with registered expected text passes
    res_pass = verifier.verify_postcondition(
        frame1, frame2, expected_text="Build Successful", min_delta_magnitude=0.0
    )
    assert res_pass.passed


def test_verified_onscreen_text_satisfies_postcondition_despite_tiny_delta():
    """Typing a short marker changes very few pixels; a verified on-screen
    deliverable must not be rejected by a whole-frame delta floor."""
    verifier = IndependentVisualVerifier()
    engine = ScreenCaptureEngine()
    frame1 = engine.render_synthetic_desktop()
    frame2 = engine.render_synthetic_desktop()
    verifier.ocr_engine.register_mock_text("GUI-OK", (300, 200, 380, 230), confidence=0.98)

    res = verifier.verify_postcondition(
        frame1, frame2, expected_text="GUI-OK", min_delta_magnitude=0.5
    )
    assert res.passed
    assert res.postcondition_checked == "GUI-OK"


def test_unverified_text_still_fails_despite_delta_floor_bypass():
    """The delta bypass must not weaken the zero-success-by-decree rule: missing
    on-screen text still fails even when the delta floor would be waived."""
    verifier = IndependentVisualVerifier()
    engine = ScreenCaptureEngine()
    frame1 = engine.render_synthetic_desktop()
    frame2 = engine.render_synthetic_desktop()

    res = verifier.verify_postcondition(
        frame1, frame2, expected_text="GUI-OK", min_delta_magnitude=0.5
    )
    assert not res.passed


def test_task_completion_certificate_tamper_evident():
    """Validates cryptographic certificate generator and verification."""
    gen = TaskCompletionCertificateGenerator()
    ver_res = IndependentVisualVerifier().verify_postcondition(
        ScreenCaptureEngine().capture_frame(),
        ScreenCaptureEngine().capture_frame(),
        min_delta_magnitude=0.0,
    )
    cert = gen.generate_certificate(
        "task_123", "emp_abc", ver_res, speedup_ratio=4.2, total_actions=10
    )

    assert cert.passed
    assert cert.verified_speedup_ratio == 4.2
    assert gen.verify_certificate(cert)

    # Tampering with score invalidates signature
    cert.score = 0.5
    assert not gen.verify_certificate(cert)


def test_gui_action_policy_strict_contract_enforcement():
    """Validates Section 2: Strict GUI-Only Interaction Contract blocks DOM/AT-SPI bypasses."""
    policy = GUIActionPolicy()

    # Prohibited shortcut attempts must raise StrictGUIContractViolation
    with pytest.raises(StrictGUIContractViolation):
        policy.validate_gui_contract("document.querySelector('#submit-btn').click()")

    with pytest.raises(StrictGUIContractViolation):
        policy.validate_gui_contract("playwright.page.goto('http://target')")

    with pytest.raises(StrictGUIContractViolation):
        policy.validate_gui_contract("at-spi accessibility_tree dump")

    # Safe GUI context passes
    policy.validate_gui_contract("Grounded target (350, 280) on raw screenshot")


def test_risk_guard_emergency_halt_and_approval_gate():
    """Validates GUIRiskGuard halts on emergency kill switch and gates destructive actions."""
    kill_switch = GlobalEmergencyKillSwitch()
    guard = GUIRiskGuard(kill_switch=kill_switch)

    # Emergency kill active blocks all dispatch in <200ms
    kill_switch.is_halted = True
    req = GUIActionRequest(
        action_id="act_kill_test",
        employee_id="emp_test",
        action_type="MOUSE_CLICK",
        confidence=0.95,
    )
    with pytest.raises(EmergencyStopTriggered):
        guard.guard_action(req)

    # Reset kill switch
    kill_switch.is_halted = False

    # Destructive action requires human approval
    req_destructive = GUIActionRequest(
        action_id="act_drop",
        employee_id="emp_test",
        action_type="MOUSE_CLICK",
        text_content="DROP_TABLE users; rm -rf /",
        confidence=0.95,
    )
    with pytest.raises(HumanApprovalRequiredError):
        guard.guard_action(req_destructive)


# ---------------------------------------------------------------------------
# 7. Benchmarks & 4x Speedup Tests
# ---------------------------------------------------------------------------


def test_human_speed_comparator_and_disqualification():
    """Validates Section 14: 4x Speedup formula and Disqualification Rule."""
    # 25s human baseline, 5s ANCOS completion -> 5.0x speedup
    res_pass = HumanSpeedComparator.evaluate_speedup(
        task_id="t_speed_1",
        task_name="Form Completion",
        human_time_seconds=25.0,
        ancos_time_seconds=5.0,
        passed_verification=True,
    )
    assert res_pass.speedup_ratio == 5.0
    assert res_pass.meets_4x_target
    assert not res_pass.disqualified

    # Disqualification Rule: If task fails verification, speedup = 0.0
    res_disqualified = HumanSpeedComparator.evaluate_speedup(
        task_id="t_speed_2",
        task_name="Form Completion",
        human_time_seconds=25.0,
        ancos_time_seconds=3.0,
        passed_verification=False,
    )
    assert res_disqualified.speedup_ratio == 0.0
    assert not res_disqualified.meets_4x_target
    assert res_disqualified.disqualified


def test_gui_success_metrics_and_reporter():
    """Validates GUISuccessMetrics tracks latency and PerformanceReporter outputs markdown/json."""
    metrics = GUISuccessMetrics()
    metrics.record_turn(
        action_type="MOUSE_CLICK",
        capture_ms=8.0,
        encoding_ms=6.0,
        grounding_ms=22.0,
        motor_ms=190.0,
        settle_ms=24.0,
    )
    metrics.record_brain_call(is_deep=False)
    summary = metrics.summarize()

    assert summary.total_actions == 1
    assert summary.average_turn_latency_ms == 250.0
    assert summary.compliance_with_300ms_budget

    md_report = PerformanceReporter.generate_markdown_report([], [], summary)
    assert "ANCOS Phase 7 GUI Intelligence Benchmark Report" in md_report
    assert "250.0ms" in md_report


# ---------------------------------------------------------------------------
# 8. Experience Compiler & Skill Certification Tests
# ---------------------------------------------------------------------------


def test_experience_recorder_and_skill_compiler():
    """Validates trajectory recording and relative anchor skill compilation."""
    recorder = ExperienceRecorder()
    recorder.start_trajectory("task_exp_1", "emp_qa", "CalcApp", "2.0")

    engine = ScreenCaptureEngine()
    frame = engine.capture_frame()
    req = GUIActionRequest(
        action_id="act_1",
        employee_id="emp_qa",
        action_type="MOUSE_CLICK",
        coordinates=(320, 200),
        confidence=0.96,
    )
    loop = GUIExecutionLoop()
    receipt = loop.execute_action(req)
    recorder.record_step(frame, req, receipt)

    traj = recorder.finalize_trajectory(verification_passed=True, verified_speedup_ratio=4.1)
    assert traj.total_actions == 1
    assert traj.verification_passed

    compiler = GUISkillCompiler()
    skill = compiler.compile_trajectory(traj, skill_name="CalcAddition")

    assert skill.skill_name == "CalcAddition"
    assert len(skill.actions) == 1
    assert "relative_pos=" in skill.actions[0].relative_anchor


def test_skill_validator_permutation_certification():
    """Validates 3-permutation held-out generalization test for skill certification."""
    registry = GUISkillRegistry()
    compiler = GUISkillCompiler()

    recorder = ExperienceRecorder()
    recorder.start_trajectory("task_cert_1", "emp_qa", "Document Editor")
    engine = ScreenCaptureEngine()
    frame = engine.capture_frame()
    req = GUIActionRequest(
        action_id="act_val_1",
        employee_id="emp_qa",
        action_type="MOUSE_CLICK",
        coordinates=(100, 160),
        confidence=0.96,
    )
    loop = GUIExecutionLoop(capture_engine=engine)
    receipt = loop.execute_action(req)
    recorder.record_step(frame, req, receipt)

    traj = recorder.finalize_trajectory(verification_passed=True, verified_speedup_ratio=4.0)
    skill = compiler.compile_trajectory(traj, "CertifiedWorkflow")
    registry.register_skill(skill)

    validator = SkillValidator(registry=registry, capture_engine=engine)
    result = validator.validate_skill(skill)

    assert result.passed
    assert result.consecutive_passes == 3
    assert result.certificate_hash is not None
    assert registry.is_certified(skill.skill_id)


def test_skill_validator_empty_skill_rejected():
    """Validates empty skill cannot be certified."""
    registry = GUISkillRegistry()
    compiler = GUISkillCompiler()
    traj = ExperienceRecorder().finalize_trajectory(True, 4.0)
    skill = compiler.compile_trajectory(traj, "EmptyWorkflow")
    validator = SkillValidator(registry=registry)
    result = validator.validate_skill(skill)
    assert not result.passed
    assert result.consecutive_passes == 0
    assert not registry.is_certified(skill.skill_id)


def test_gui_execution_loop_safety_blocking():
    """Validates GUIExecutionLoop blocks destructive actions and kill switch triggers."""
    kill_switch = GlobalEmergencyKillSwitch()
    guard = GUIRiskGuard(kill_switch=kill_switch)
    loop = GUIExecutionLoop(risk_guard=guard)

    # 1. Kill switch halted blocks action
    kill_switch.is_halted = True
    req = GUIActionRequest(
        action_id="act_kill_exec",
        employee_id="emp_1",
        action_type="MOUSE_CLICK",
        coordinates=(100, 100),
        confidence=0.95,
    )
    receipt = loop.execute_action(req)
    assert receipt.status == "BLOCKED_BY_SAFETY"
    assert "Emergency Stop" in (receipt.error_message or "")

    kill_switch.is_halted = False

    # 2. Destructive command blocked
    req_destructive = GUIActionRequest(
        action_id="act_destruct_exec",
        employee_id="emp_1",
        action_type="KEY_TYPE",
        text_content="DROP_TABLE users; rm -rf /",
        confidence=0.95,
    )
    receipt_dest = loop.execute_action(req_destructive)
    assert receipt_dest.status == "BLOCKED_BY_SAFETY"

    # 3. Prohibited shortcut (CDP / DOM) blocked
    req_dom = GUIActionRequest(
        action_id="act_dom_exec",
        employee_id="emp_1",
        action_type="KEY_TYPE",
        text_content="document.querySelector('#btn').click()",
        confidence=0.95,
    )
    receipt_dom = loop.execute_action(req_dom)
    assert receipt_dom.status == "BLOCKED_BY_SAFETY"


def test_gui_execution_loop_missing_coordinates_blocked():
    """Validates missing coordinates for mouse actions are blocked."""
    loop = GUIExecutionLoop()
    req_no_coords = GUIActionRequest(
        action_id="act_no_coords",
        employee_id="emp_1",
        action_type="MOUSE_CLICK",
        coordinates=None,
        confidence=0.95,
    )
    receipt = loop.execute_action(req_no_coords)
    assert receipt.status == "BLOCKED_BY_SAFETY"
    assert "requires explicit coordinates" in (receipt.error_message or "")


@pytest.mark.asyncio
async def test_dual_brain_cognitive_controller_failure_not_masked():
    """Validates failing steps are not marked as completed and task fails."""
    controller = DualBrainCognitiveController()
    kill_switch = GlobalEmergencyKillSwitch()
    kill_switch.is_halted = True
    controller.execution_loop.risk_guard.kill_switch = kill_switch

    result = await controller.execute_task("Save document to disk", max_steps=2)
    assert result["success"] is False
    assert result["completed_steps"] == 0


def test_gui_action_policy_expanded_prohibited_shortcuts():
    """Validates that all prohibited bypasses raise StrictGUIContractViolation."""
    policy = GUIActionPolicy()
    prohibited_samples = [
        "document.querySelectorAll('button')",
        "window.__ancos_hook()",
        "page.locator('.save-btn').click()",
        "xdotool click 1",
        "os.system('id')",
        "os.popen('whoami')",
        "shutil.rmtree('/tmp/work')",
        "__import__('shutil').rmtree('.')",
    ]
    for sample in prohibited_samples:
        with pytest.raises(StrictGUIContractViolation):
            policy.validate_gui_contract(sample)


@pytest.mark.asyncio
async def test_dual_brain_cognitive_controller_fails_closed_on_cheating_contract_violation():
    """Validates that cheating in a planned step fails closed with StrictGUIContractViolation."""
    planner = System2TaskPlanner()
    planner.set_custom_plan_generator(
        lambda obj, b: [
            PlanStep(
                step_id="step_cheat",
                phase_name="Cheat Attempt",
                action_intent="CLICK",
                target_descriptor="document.querySelector('#hidden_button')",
                expected_postcondition="File saved",
            )
        ]
    )
    controller = DualBrainCognitiveController(deep_planner=planner)
    with pytest.raises(StrictGUIContractViolation):
        await controller.execute_task("Cheat by DOM", max_steps=5)


@pytest.mark.asyncio
async def test_dual_brain_cognitive_controller_negative_or_zero_max_steps():
    """Validates that non-positive max_steps does not slice negatively and fails cleanly."""
    controller = DualBrainCognitiveController()
    result_neg = await controller.execute_task("Save file", max_steps=-1)
    assert result_neg["success"] is False
    assert result_neg["steps_attempted"] == 0
    assert result_neg["plan_truncated"] is True

    result_zero = await controller.execute_task("Save file", max_steps=0)
    assert result_zero["success"] is False
    assert result_zero["steps_attempted"] == 0
    assert result_zero["plan_truncated"] is True


def test_gui_execution_loop_coordinates_out_of_bounds():
    """Validates that coordinates exceeding screen dimensions are blocked."""
    loop = GUIExecutionLoop()
    req_oob = GUIActionRequest(
        action_id="act_oob",
        employee_id="emp_test",
        action_type="MOUSE_CLICK",
        coordinates=(9999, 9999),
        confidence=0.95,
    )
    receipt = loop.execute_action(req_oob)
    assert receipt.status == "BLOCKED_BY_SAFETY"
    assert "exceed screen resolution" in (receipt.error_message or "")

    req_drag_oob = GUIActionRequest(
        action_id="act_drag_oob",
        employee_id="emp_test",
        action_type="MOUSE_DRAG",
        coordinates=(100, 100),
        drag_end_coordinates=(5000, 5000),
        confidence=0.95,
    )
    receipt_drag = loop.execute_action(req_drag_oob)
    assert receipt_drag.status == "BLOCKED_BY_SAFETY"
    assert "drag end coordinates" in (receipt_drag.error_message or "")


def test_visual_grounder_ungrounded_never_returns_screen_center():
    """Validates ungrounded query returns invalid coordinates (-1, -1) and never screen center."""
    engine = ScreenCaptureEngine()
    frame = engine.render_synthetic_desktop(resolution=(1280, 800))
    grounder = VisualGrounder()
    target = grounder.ground_query(frame, "NonexistentTarget", require_confidence=False)
    assert target.is_confident is False
    assert target.coordinates == (-1, -1)
    assert target.coordinates != (640, 400)


def test_pixel_ocr_find_text_empty_query_returns_empty():
    """Validates empty or whitespace query in find_text returns empty list."""
    engine = ScreenCaptureEngine()
    frame = engine.render_synthetic_desktop()
    ocr = PixelOCREngine()
    assert ocr.find_text(frame, "") == []
    assert ocr.find_text(frame, "   ") == []


def test_gui_skill_registry_thread_safety():
    """Validates concurrent access to GUISkillRegistry across multiple threads."""
    import concurrent.futures

    from neural.gui_intelligence.skills.skill_compiler import CompiledGUISkill, RelativeGUIAction

    registry = GUISkillRegistry()

    def worker(idx: int):
        skill = CompiledGUISkill(
            skill_id=f"skill_thread_{idx}",
            skill_name=f"Skill {idx}",
            description="Concurrent test skill",
            application_name="App",
            actions=[RelativeGUIAction(0, "MOUSE_CLICK", "anchor", (0.1, 0.1))],
        )
        registry.register_skill(skill)
        assert registry.get_skill(f"skill_thread_{idx}") is not None
        registry.mark_certified(f"skill_thread_{idx}")
        assert registry.is_certified(f"skill_thread_{idx}") is True
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(20)]
        results = [f.result() for f in futures]
    assert all(results)
    assert len(registry.list_skills(only_certified=True)) == 20


def test_screen_capture_engine_escape_clears_dialog_pixels():
    """Validates that pressing Escape key actually removes dialog pixels from the frame buffer."""
    engine = ScreenCaptureEngine()
    frame_with_dialog = engine.render_multi_app_desktop(active_app="editor", include_dialog=True)
    engine.apply_visual_action("KEY_HOTKEY", text="Escape")
    frame_after = engine.capture_frame()

    detector = ScreenChangeDetector()
    delta = detector.compute_delta(frame_with_dialog, frame_after)
    assert delta.magnitude > 0.001
    elements = ScreenCaptureEngine.get_last_rendered_elements()
    assert not any(el.get("type") == "dialog" for el in elements)
