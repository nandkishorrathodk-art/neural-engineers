from __future__ import annotations

import math
import time
from typing import Any, Optional

from neural.exceptions import VisualUncertaintyError
from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop
from neural.gui_intelligence.brain.fast_policy import System1FastPolicy
from neural.gui_intelligence.brain.task_planner import CompiledExecutionPlan, System2TaskPlanner
from neural.gui_intelligence.models import (
    GUIActionReceipt,
    GUIActionRequest,
)
from neural.gui_intelligence.perception.object_detection import UIObjectDetector
from neural.gui_intelligence.perception.screen_capture import ScreenCaptureEngine
from neural.gui_intelligence.recovery.failure_detector import GUIFailureDetector
from neural.gui_intelligence.recovery.state_recovery import GUIStateRecovery
from neural.gui_intelligence.recovery.visibility_recovery import VisibilityRecoveryEngine
from neural.gui_intelligence.skills.experience_recorder import ExperienceRecorder
from neural.gui_intelligence.verification.visual_verifier import (
    IndependentVisualVerifier,
    VisualVerificationResult,
)
from neural.gui_intelligence.world_model.visual_belief import VisualBeliefStateManager
from neural.gui_intelligence.world_model.window_state import WindowStateTracker
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.brain.cognitive_controller")
MIN_POSTCONDITION_DELTA = 0.00001


class DualBrainCognitiveController:
    """
    Dual-Brain Cognitive Engine Controller (Section 4).
    Arbitrates between:
    - System 2 Deep Brain (Strategic high-level VLM planning, recovery & verification)
    - System 1 Fast Brain (Sub-50ms local grounding, action chunking & smooth motor dispatch)
    Enforces automatic escalation to System 2 when visual uncertainty > 0.12 or anomalies appear.
    """

    def __init__(
        self,
        deep_planner: Optional[System2TaskPlanner] = None,
        fast_policy: Optional[System1FastPolicy] = None,
        execution_loop: Optional[GUIExecutionLoop] = None,
        belief_manager: Optional[VisualBeliefStateManager] = None,
        capture_engine: Optional[ScreenCaptureEngine] = None,
        failure_detector: Optional[GUIFailureDetector] = None,
        visibility_recovery: Optional[VisibilityRecoveryEngine] = None,
        state_recovery: Optional[GUIStateRecovery] = None,
        experience_recorder: Optional[ExperienceRecorder] = None,
        window_tracker: Optional[WindowStateTracker] = None,
        detector: Optional[UIObjectDetector] = None,
        visual_verifier: Optional[IndependentVisualVerifier] = None,
    ) -> None:
        self.capture_engine = capture_engine or ScreenCaptureEngine()
        self.deep_planner = deep_planner or System2TaskPlanner()
        self.fast_policy = fast_policy or System1FastPolicy()
        self.execution_loop = execution_loop or GUIExecutionLoop(capture_engine=self.capture_engine)
        self.belief_manager = belief_manager or VisualBeliefStateManager()
        self.detector = detector or UIObjectDetector()
        self.window_tracker = window_tracker or WindowStateTracker()
        self.failure_detector = failure_detector or GUIFailureDetector()
        self.visibility_recovery = visibility_recovery or VisibilityRecoveryEngine(
            mouse_controller=self.execution_loop.mouse_controller,
            keyboard_controller=self.execution_loop.keyboard_controller,
            capture_engine=self.capture_engine,
        )
        self.state_recovery = state_recovery or GUIStateRecovery(
            keyboard_controller=self.execution_loop.keyboard_controller,
            mouse_controller=self.execution_loop.mouse_controller,
        )
        self.experience_recorder = experience_recorder
        self.visual_verifier = visual_verifier or IndependentVisualVerifier()

        # Telemetry & metrics
        self.deep_brain_invocations: int = 0
        self.fast_brain_invocations: int = 0
        self.escalation_count: int = 0
        self.executed_receipts: list[GUIActionReceipt] = []

    async def execute_task(
        self,
        task_objective: str,
        max_steps: int = 20,
    ) -> dict[str, Any]:
        """
        Executes a complete GUI task using the dual-brain cognitive architecture.
        """
        start_time = time.perf_counter()
        self.deep_brain_invocations += 1

        # 1. System 2: Initial Strategic Planning
        initial_belief = self.belief_manager.get_state()
        plan: CompiledExecutionPlan = await self.deep_planner.create_plan(
            task_objective=task_objective,
            belief_state=initial_belief,
        )

        completed_steps = 0
        escalations = 0
        verification_receipts: list[dict[str, Any]] = []

        target_steps = plan.steps[:max_steps]
        plan_truncated = len(target_steps) < len(plan.steps)
        for step in target_steps:
            current_frame = self.capture_engine.capture_frame()

            # Dynamic perception update: detect elements, update windows and belief
            elements = self.detector.detect(current_frame)
            visible_windows = self.window_tracker.update_from_elements(current_frame, elements)
            active_dialogs = self.window_tracker.detect_active_dialogs()
            self.belief_manager.update_belief(
                frame=current_frame,
                visible_windows=visible_windows,
                active_dialogs=active_dialogs,
            )

            # Check if belief state requires escalation before dispatch
            if self.belief_manager.requires_escalation:
                self.escalation_count += 1
                escalations += 1
                logger.warning(
                    "Belief state uncertainty high; escalating step to System 2 / Recovery",
                    step=step.step_id,
                    uncertainty=self.belief_manager.uncertainty_score,
                )
                # Attempt recovery using VisibilityRecovery / StateRecovery
                if active_dialogs:
                    rec_info = self.visibility_recovery.recover_control_visibility(
                        target_descriptor=step.target_descriptor,
                        visible_windows=visible_windows,
                        active_dialogs=active_dialogs,
                        scrollable_elements=[e for e in elements if e.element_type == "scrollbar"],
                        menu_expanders=[e for e in elements if e.element_type == "menu"],
                    )
                    logger.info("Visibility recovery action taken", **rec_info)
                else:
                    self.state_recovery.dismiss_modal()

                # Re-capture after recovery attempt
                current_frame = self.capture_engine.capture_frame()
                elements = self.detector.detect(current_frame)
                visible_windows = self.window_tracker.update_from_elements(current_frame, elements)
                active_dialogs = self.window_tracker.detect_active_dialogs()
                self.belief_manager.update_belief(
                    frame=current_frame,
                    visible_windows=visible_windows,
                    active_dialogs=active_dialogs,
                )

                if self.belief_manager.requires_escalation:
                    # Recovery did not resolve uncertainty; abort step without counting success
                    continue

            # 2. System 1: Fast Brain Action Chunk Generation
            self.fast_brain_invocations += 1
            chunk: list[GUIActionRequest] = []
            try:
                chunk = self.fast_policy.select_action_chunk(
                    step=step,
                    frame=current_frame,
                    belief_state=self.belief_manager.get_state(),
                )
            except VisualUncertaintyError as e:
                # Fast brain failed confidence threshold -> escalate to System 2
                self.escalation_count += 1
                escalations += 1
                logger.warning("Fast policy uncertainty escalation", error=str(e))
                # No screen-center or guessed-coordinate fallback is permitted.
                # Without a grounded action, this step remains incomplete.
                logger.warning("No visually grounded action available; aborting step", step=step.step_id)
                verification_receipts.append({
                    "step_id": step.step_id,
                    "verified": False,
                    "reason": "visual grounding did not produce an action",
                })
                continue

            # 3. Motor Execution Loop: Execute chunk
            chunk_receipts = self.execution_loop.execute_chunk(chunk)
            for req, rec in zip(chunk, chunk_receipts):
                if self.experience_recorder:
                    self.experience_recorder.record_step(current_frame, req, rec)
                self.executed_receipts.append(rec)

            # Check if any receipt halted due to anomaly
            failed_receipts = [r for r in chunk_receipts if r.status != "SUCCESS"]
            if failed_receipts:
                self.escalation_count += 1
                escalations += 1
                logger.warning(
                    "Action chunk experienced failure; escalating to Deep Brain",
                    failed_status=failed_receipts[0].status,
                )
                # Failure recovery: diagnose and dismiss modal/state
                post_frame = self.capture_engine.capture_frame()
                diag = self.failure_detector.diagnose(
                    frame_before=current_frame,
                    frame_after=post_frame,
                    active_dialogs=self.window_tracker.detect_active_dialogs(),
                    action_type=chunk[0].action_type if chunk else "UNKNOWN",
                )
                logger.info("Failure diagnosed", failure_type=diag.failure_type, severity=diag.severity)
                self.state_recovery.dismiss_modal()
                # DO NOT increment completed_steps on failure!
                verification_receipts.append({
                    "step_id": step.step_id,
                    "verified": False,
                    "reason": "one or more GUI action receipts failed",
                })
                continue

            # A motor receipt is not task evidence. Independently inspect the
            # post-action raster and require a valid verifier receipt per step.
            post_frame = self.capture_engine.capture_frame()
            expected_text = step.input_value if step.action_intent.upper() in {"TYPE", "KEY_TYPE"} else None
            try:
                verification: VisualVerificationResult | None = self.visual_verifier.verify_postcondition(
                    frame_initial=current_frame,
                    frame_final=post_frame,
                    expected_text=expected_text,
                    min_delta_magnitude=MIN_POSTCONDITION_DELTA,
                )
            except Exception as exc:
                verification = None
                verification_error = str(exc)
            else:
                verification_error = None

            valid_verification = bool(
                isinstance(step.expected_postcondition, str)
                and bool(step.expected_postcondition.strip())
                and verification
                and verification.passed is True
                and isinstance(verification.score, (int, float))
                and math.isfinite(verification.score)
                and verification.score >= 0.5
                and verification.screenshot_hash == post_frame.frame_hash
                and isinstance(verification.postcondition_checked, str)
                and bool(verification.postcondition_checked.strip())
                and isinstance(verification.delta_magnitude, (int, float))
                and math.isfinite(verification.delta_magnitude)
                and verification.delta_magnitude >= MIN_POSTCONDITION_DELTA
            )
            verification_receipts.append({
                "step_id": step.step_id,
                "expected_postcondition": step.expected_postcondition,
                "verified": valid_verification,
                "score": verification.score if verification else 0.0,
                "delta_magnitude": verification.delta_magnitude if verification else 0.0,
                "screenshot_hash": verification.screenshot_hash if verification else None,
                "details": verification.details if verification else verification_error,
            })
            if not valid_verification:
                self.escalation_count += 1
                escalations += 1
                logger.warning(
                    "Independent visual postcondition verification failed; step remains incomplete",
                    step=step.step_id,
                    reason=verification_error or (
                        verification.details if verification else "visual verifier returned no receipt"
                    ),
                )
                continue

            completed_steps += 1

        total_elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        # A caller-imposed step limit is not proof that the full planned task
        # completed. Keep partial execution visible, but never certify it.
        success = (
            not plan_truncated
            and completed_steps > 0
            and completed_steps == len(plan.steps)
        )

        return {
            "task_objective": task_objective,
            "success": success,
            "plan_id": plan.plan_id,
            "total_steps_in_plan": len(plan.steps),
            "steps_attempted": len(target_steps),
            "plan_truncated": plan_truncated,
            "completed_steps": completed_steps,
            "verification_receipts": verification_receipts,
            "escalations": escalations,
            "deep_brain_calls": self.deep_brain_invocations,
            "fast_brain_calls": self.fast_brain_invocations,
            "total_actions_dispatched": len(self.executed_receipts),
            "total_latency_ms": total_elapsed_ms,
            "average_ms_per_action": (
                total_elapsed_ms / len(self.executed_receipts)
                if self.executed_receipts
                else 0.0
            ),
        }

