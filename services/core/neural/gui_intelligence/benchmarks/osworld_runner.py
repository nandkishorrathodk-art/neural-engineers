from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

from neural.gui_intelligence.brain.cognitive_controller import DualBrainCognitiveController
from neural.gui_intelligence.perception.screen_capture import ScreenCaptureEngine
from neural.gui_intelligence.verification.visual_verifier import IndependentVisualVerifier
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.benchmarks.osworld_runner")


@dataclass
class OSWorldTaskResult:
    task_id: str
    task_name: str
    category: str
    passed: bool
    score: float
    total_steps: int
    elapsed_time_seconds: float
    average_step_latency_ms: float
    error_message: Optional[str] = None
    details: dict[str, Any] = None  # type: ignore

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


class OSWorldRunner:
    """
    OSWorld 2.1 Benchmark Protocol Runner (Section 3 & Section 14).
    Executes standard OS benchmark tasks across simulated and live Linux desktop environments.
    Strictly follows the GUI-Only Interaction Contract (zero DOM, zero AT-SPI).
    """

    def __init__(
        self,
        controller: Optional[DualBrainCognitiveController] = None,
        capture_engine: Optional[ScreenCaptureEngine] = None,
        verifier: Optional[IndependentVisualVerifier] = None,
    ) -> None:
        self.controller = controller or DualBrainCognitiveController()
        self.capture_engine = capture_engine or ScreenCaptureEngine()
        self.verifier = verifier or IndependentVisualVerifier()

    async def run_task(
        self,
        task_id: str,
        task_name: str,
        task_objective: str,
        category: str = "daily_workflows",
        expected_deliverable_text: Optional[str] = None,
        max_steps: int = 15,
    ) -> OSWorldTaskResult:
        """
        Executes an OSWorld benchmark task and evaluates completion criteria.
        """
        start = time.perf_counter()
        initial_frame = self.capture_engine.capture_frame()

        logger.info(
            "Starting OSWorld benchmark task",
            task_id=task_id,
            task_name=task_name,
            category=category,
        )

        error_msg = None
        execution_meta = {}

        try:
            execution_meta = await self.controller.execute_task(
                task_objective=task_objective,
                max_steps=max_steps,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error("OSWorld task execution failed with error", error=error_msg)

        final_frame = self.capture_engine.capture_frame()
        elapsed_sec = time.perf_counter() - start

        # Evaluate verification postcondition
        verification = self.verifier.verify_postcondition(
            frame_initial=initial_frame,
            frame_final=final_frame,
            expected_text=expected_deliverable_text,
            min_delta_magnitude=0.0,
        )

        steps_done = execution_meta.get("completed_steps", 0)
        total_actions = execution_meta.get("total_actions_dispatched", 1)
        avg_step_ms = (elapsed_sec * 1000.0) / max(1, total_actions)

        passed = verification.passed and error_msg is None

        return OSWorldTaskResult(
            task_id=task_id,
            task_name=task_name,
            category=category,
            passed=passed,
            score=verification.score if passed else 0.0,
            total_steps=steps_done,
            elapsed_time_seconds=elapsed_sec,
            average_step_latency_ms=avg_step_ms,
            error_message=error_msg,
            details={
                "verification": verification.details,
                "screenshot_hash": final_frame.frame_hash,
                "cognitive_meta": execution_meta,
            },
        )
