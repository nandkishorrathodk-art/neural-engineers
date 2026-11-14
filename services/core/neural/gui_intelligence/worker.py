"""Real GUI worker engine: executes a GUI task against a live employee desktop.

This is the missing execution mode (gap G-1.1). It:

1. binds to the employee's *container* desktop (never the core API host) and
   asserts the X display and the scrot/xdotool binaries are actually present;
2. perceives the desktop as raster pixels captured from the container;
3. grounds each plan step to screen coordinates with real OCR over those pixels;
4. dispatches real pointer and keyboard events into the container's X server
   through the cosine-eased motor controllers;
5. verifies the postcondition against the final framebuffer, independently of the
   actions taken;
6. returns a receipt carrying before/after screenshot hashes and the plan.

No synthetic pixels, no DOM, no accessibility tree, no hidden CLI shortcuts.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop
from neural.gui_intelligence.models import GUIActionReceipt, GUIActionRequest
from neural.gui_intelligence.perception.ocr_runtime import TesseractOCREngine
from neural.gui_intelligence.perception.visual_grounding import VisualGrounder
from neural.gui_intelligence.runtime.desktop_driver import ContainerDesktopDriver
from neural.gui_intelligence.runtime.motor import (
    RealDesktopCaptureEngine,
    build_real_motor_controllers,
)
from neural.gui_intelligence.safety.risk_guard import GUIRiskGuard
from neural.gui_intelligence.verification.visual_verifier import IndependentVisualVerifier
from neural.logger import get_logger
from neural.verification.visual_match import frame_dhash

logger = get_logger("ancos.gui_intelligence.worker")


class GuiWorkerExecutionError(RuntimeError):
    """Raised when a GUI task cannot be executed or its postcondition fails."""


@dataclass
class GuiStepPlan:
    action_type: str
    target: Optional[str] = None
    text: Optional[str] = None
    key_code: Optional[str] = None
    coordinates: Optional[tuple[int, int]] = None
    confidence: float = 0.95
    expected_postcondition: Optional[str] = None
    # When True, ``KEY_TYPE`` sends keystrokes to whatever window currently holds
    # keyboard focus instead of clicking the last grounded coordinate first. A
    # GTK dialog location bar (opened with Ctrl+L) owns focus already; the
    # default click-before-type would click back onto the document and drop that
    # focus, silently typing nothing into the dialog.
    type_into_active_focus: bool = False
    # Duration for a WAIT_SETTLE step, in milliseconds. A fixed 50ms is too short
    # for a file dialog to hand focus back to the editor after Return; the next
    # keystroke then lands in the dialog instead of the document.
    settle_ms: Optional[int] = None
    # CONFIRM_SAVED: file name the saved document's title must contain. Empty
    # means "any unmodified document window confirms the save".
    expected_text: Optional[str] = None


@dataclass
class GuiWorkerResult:
    status: str
    employee_id: str
    steps_executed: int
    receipts: list[dict] = field(default_factory=list)
    screen_hash_before: Optional[str] = None
    screen_hash_after: Optional[str] = None
    screen_phash_after: Optional[str] = None
    verification: Optional[dict] = None
    error: Optional[str] = None
    # Wall-clock attribution for the whole run, in milliseconds, split by phase:
    # "grounding_ms" (fresh-frame capture + OCR target search per step),
    # "step_ms" (sum of per-action receipt execution), "verification_ms" (final
    # independent verifier), "run_ms" (total). Measured, not estimated.
    timings: dict[str, float] = field(default_factory=dict)
    # Cumulative OCR counters for the run: full-frame vs region calls, cache
    # hits/misses, and time split by path. Additive; consumers ignore if unknown.
    ocr_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "employee_id": self.employee_id,
            "steps_executed": self.steps_executed,
            "receipts": self.receipts,
            "screen_hash_before": self.screen_hash_before,
            "screen_hash_after": self.screen_hash_after,
            "screen_phash_after": self.screen_phash_after,
            "verification": self.verification,
            "error": self.error,
            "timings": self.timings,
            "ocr_metrics": self.ocr_metrics,
        }


class GuiWorkerEngine:
    """Executes GUI steps on an employee container desktop and returns receipts."""

    def __init__(
        self,
        container_manager: Any,
        display: str = ":1",
        require_real_desktop: bool = True,
        enable_region_hints: bool = True,
    ) -> None:
        self.container_manager = container_manager
        self.display = display
        self.require_real_desktop = require_real_desktop
        # When enabled, a target already located once is re-grounded inside its
        # last known region instead of re-reading the whole screen. Purely a
        # performance optimization: the region result is still validated, and a
        # weak region match falls back to a full-frame read, so correctness is
        # unchanged. Kept switchable so the optimization can be A/B measured.
        self.enable_region_hints = enable_region_hints

    def _build_loop(
        self,
        driver: ContainerDesktopDriver,
        resolution: tuple[int, int],
    ) -> GUIExecutionLoop:
        capture = RealDesktopCaptureEngine(driver)
        mouse, keyboard = build_real_motor_controllers(driver, screen_resolution=resolution)
        grounder = VisualGrounder(ocr_engine=TesseractOCREngine())
        return GUIExecutionLoop(
            capture_engine=capture,  # type: ignore[arg-type]
            mouse_controller=mouse,
            keyboard_controller=keyboard,
            grounder=grounder,
            risk_guard=GUIRiskGuard(),
            window_state_provider=driver.window_titles,
        )

    @staticmethod
    def _hash_frame(frame) -> str:
        return hashlib.sha256(frame.pixels.tobytes()).hexdigest()

    def execute(
        self,
        employee_id: str,
        docker_id: str,
        steps: list[GuiStepPlan | dict],
        expected_text: Optional[str] = None,
        expected_postcondition: Optional[str] = None,
        min_delta_magnitude: float = 0.0005,
    ) -> GuiWorkerResult:
        run_start = time.perf_counter()
        timings: dict[str, float] = {}
        driver = ContainerDesktopDriver(self.container_manager, docker_id, display=self.display)
        if self.require_real_desktop:
            driver.assert_available()
            driver.reset_editor_state()

        frame_before = driver.capture_frame()
        hash_before = self._hash_frame(frame_before)
        loop = self._build_loop(driver, frame_before.resolution)

        receipts: list[dict] = []
        executed = 0
        last_coordinates: Optional[tuple[int, int]] = None
        # Last screen region each target was grounded in. Re-grounding the *same*
        # target (a retry, or a later step) can then look only inside that region
        # instead of re-reading the whole screen. Keyed by target text so a hint is
        # only ever used for a target we have actually located before.
        target_regions: dict[str, Any] = {}
        ocr_metrics: dict[str, float] = {}
        for raw_step in steps:
            step = raw_step if isinstance(raw_step, GuiStepPlan) else GuiStepPlan(**raw_step)
            # Ground against a fresh frame: a previous step (e.g. opening the
            # application menu) changes what is visible, so a target that only
            # appears afterwards cannot be located on the task-start frame.
            hint = (
                target_regions.get(step.target)
                if (step.target and self.enable_region_hints)
                else None
            )
            _ground_start = time.perf_counter()
            request = self._to_request_with_fresh_frame(
                driver,
                employee_id,
                step,
                grounder=loop.grounder,
                fallback_coordinates=last_coordinates,
                region_hint=hint,
            )
            # Fresh-frame capture + OCR target search is a real per-step cost that
            # happens before the action loop; attribute it separately.
            timings["grounding_ms"] = timings.get("grounding_ms", 0.0) + (
                time.perf_counter() - _ground_start
            ) * 1000.0
            self._accumulate_ocr_metrics(ocr_metrics, loop.grounder)
            grounded = getattr(loop.grounder, "last_target", None)
            if step.target and grounded is not None and grounded.bounding_box != (0, 0, 0, 0):
                region = VisualGrounder.region_from_box(
                    grounded.bounding_box, frame_before.resolution
                )
                if region is not None:
                    target_regions[step.target] = region
            _step_start = time.perf_counter()
            receipt: GUIActionReceipt = loop.execute_action(request)
            timings["step_ms"] = timings.get("step_ms", 0.0) + (
                time.perf_counter() - _step_start
            ) * 1000.0
            receipts.append(receipt.model_dump())
            executed += 1
            if request.coordinates is not None:
                last_coordinates = request.coordinates
            if receipt.status != "SUCCESS":
                logger.warning(
                    "GUI step did not succeed; halting task",
                    employee_id=employee_id,
                    action_id=request.action_id,
                    status=receipt.status,
                )
                break

        frame_after = driver.capture_frame()
        hash_after = self._hash_frame(frame_after)
        phash_after = frame_dhash(frame_after.pixels)

        _verify_start = time.perf_counter()
        verifier = IndependentVisualVerifier(ocr_engine=TesseractOCREngine())
        verification = verifier.verify_postcondition(
            frame_initial=frame_before,
            frame_final=frame_after,
            expected_text=expected_text,
            min_delta_magnitude=min_delta_magnitude,
            expected_postcondition=expected_postcondition,
        )
        timings["verification_ms"] = (time.perf_counter() - _verify_start) * 1000.0
        verification_dict = {
            "passed": verification.passed,
            "score": verification.score,
            "delta_magnitude": verification.delta_magnitude,
            "postcondition_checked": verification.postcondition_checked,
            "details": verification.details,
            "screenshot_hash": verification.screenshot_hash,
        }

        all_success = executed == len(steps) and all(r["status"] == "SUCCESS" for r in receipts)
        status = "success" if (all_success and verification.passed) else "failed"
        error = None
        if status == "failed":
            error = (
                verification.details
                if all_success
                else next(
                    (r["error_message"] for r in receipts if r["status"] != "SUCCESS"),
                    "GUI step failed",
                )
            )

        timings["run_ms"] = (time.perf_counter() - run_start) * 1000.0
        result = GuiWorkerResult(
            status=status,
            employee_id=employee_id,
            steps_executed=executed,
            receipts=receipts,
            screen_hash_before=hash_before,
            screen_hash_after=hash_after,
            screen_phash_after=phash_after,
            verification=verification_dict,
            error=error,
            timings=timings,
            ocr_metrics=ocr_metrics,
        )
        if status == "failed":
            logger.error("GUI task failed", employee_id=employee_id, error=error)
        return result

    def _to_request_with_fresh_frame(
        self,
        driver: ContainerDesktopDriver,
        employee_id: str,
        step: GuiStepPlan,
        grounder: VisualGrounder,
        fallback_coordinates: Optional[tuple[int, int]] = None,
        region_hint=None,
    ) -> GUIActionRequest:
        """Resolve a step against a freshly captured frame, retrying while a target settles.

        Opening a menu or window is asynchronous: the first capture after a click can
        still show the old frame. Re-capturing briefly gives the desktop time to render
        the target before the grounder is asked to locate it.
        """
        attempts = 6 if step.target else 1
        last_error: Optional[GuiWorkerExecutionError] = None
        for attempt in range(attempts):
            frame = driver.capture_frame()
            try:
                return self._to_request(
                    employee_id,
                    step,
                    frame=frame,
                    grounder=grounder,
                    fallback_coordinates=fallback_coordinates,
                    region_hint=region_hint,
                )
            except GuiWorkerExecutionError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(0.5)
        assert last_error is not None
        raise last_error

    def _to_request(
        self,
        employee_id: str,
        step: GuiStepPlan,
        frame,
        grounder: VisualGrounder,
        fallback_coordinates: Optional[tuple[int, int]] = None,
        region_hint=None,
    ) -> GUIActionRequest:
        coordinates = step.coordinates
        if coordinates is None and step.target:
            coordinates = self._ground(grounder, frame, step.target, region_hint=region_hint)
        if (
            coordinates is None
            and step.action_type == "KEY_TYPE"
            and not step.type_into_active_focus
            and fallback_coordinates is not None
        ):
            # Text entry must follow a verified click. Reuse the most recent click
            # coordinate so the keyboard focus gate has a concrete target instead of
            # failing closed on a real desktop that has no inheritable focus. When
            # the step targets active focus (a dialog already owns it), reuse is
            # wrong: the click would steal focus back from the dialog.
            coordinates = fallback_coordinates
        payload: dict[str, Any] = {
            "action_id": f"gui_{uuid.uuid4().hex[:10]}",
            "employee_id": employee_id,
            "action_type": step.action_type,
            "confidence": step.confidence,
        }
        if step.type_into_active_focus:
            payload["active_focus"] = True
        if step.settle_ms is not None:
            payload["settle_ms"] = step.settle_ms
        if step.expected_text is not None:
            payload["expected_text"] = step.expected_text
        if coordinates is not None:
            payload["coordinates"] = coordinates
        if step.text is not None:
            payload["text_content"] = step.text
        if step.key_code is not None:
            payload["key_code"] = step.key_code
        return GUIActionRequest(**payload)

    @staticmethod
    def _ground(
        grounder: VisualGrounder,
        frame,
        target: str,
        region_hint=None,
    ) -> tuple[int, int]:
        grounded = (
            grounder.ground_query(frame, target, region_hint=region_hint)
            if region_hint is not None
            else grounder.ground_query(frame, target)
        )
        coordinates = getattr(grounded, "coordinates", None) if grounded else None
        if grounded is None or coordinates is None or coordinates[0] < 0 or coordinates[1] < 0:
            # ground_query returns (-1, -1) when nothing matched; clicking that would
            # be acting blind, so refuse instead of dispatching a bogus click.
            raise GuiWorkerExecutionError(
                f"Could not ground GUI target '{target}' on the live desktop; refusing to act blind."
            )
        return coordinates

    @staticmethod
    def _accumulate_ocr_metrics(acc: dict[str, float], grounder: VisualGrounder) -> None:
        """Merge the grounder's OCR engine cumulative counters into ``acc``.

        The counters are cumulative for the engine's lifetime, so the latest
        snapshot already reflects every OCR call made so far in this run.
        """
        engine = getattr(grounder, "ocr_engine", None)
        snapshot = getattr(engine, "metrics_snapshot", None)
        if snapshot is None:
            return
        acc.update(snapshot())


def build_gui_worker_engine(
    container_manager: Any,
    display: str = ":1",
    require_real_desktop: bool = True,
) -> GuiWorkerEngine:
    """Construct a GUI worker engine bound to an employee container manager."""
    return GuiWorkerEngine(
        container_manager=container_manager,
        display=display,
        require_real_desktop=require_real_desktop,
    )


__all__ = [
    "GuiStepPlan",
    "GuiWorkerEngine",
    "GuiWorkerExecutionError",
    "GuiWorkerResult",
    "build_gui_worker_engine",
]
