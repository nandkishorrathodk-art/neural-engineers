from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from neural.gui_intelligence.models import (
    ExperienceTrajectoryRecord,
    GUIActionReceipt,
    GUIActionRequest,
    ScreenFrame,
    utc_now_iso,
)
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.skills.experience_recorder")


class ExperienceRecorder:
    """
    GUI Experience & Trajectory Recorder (Section 7 & 19).
    Records timestamped execution traces:
    - Raw screenshot hashes
    - Dispatched actions and coordinate parameters
    - Action execution receipts
    - Independent verification outcome and speedup ratio
    """

    def __init__(self) -> None:
        self._current_trajectory_id: Optional[str] = None
        self._task_id: Optional[str] = None
        self._employee_id: Optional[str] = None
        self._app_name: str = "DesktopApp"
        self._app_version: str = "1.0.0"
        self._resolution: tuple[int, int] = (1280, 800)
        self._start_time: str = ""
        self._actions: list[dict[str, Any]] = []
        self._screenshot_hashes: list[str] = []

    def start_trajectory(
        self,
        task_id: str,
        employee_id: str,
        application_name: str = "DesktopApp",
        application_version: str = "1.0.0",
        screen_resolution: tuple[int, int] = (1280, 800),
    ) -> str:
        """Initializes a new recording session."""
        self._current_trajectory_id = f"traj_{uuid.uuid4().hex[:12]}"
        self._task_id = task_id
        self._employee_id = employee_id
        self._app_name = application_name
        self._app_version = application_version
        self._resolution = screen_resolution
        self._start_time = utc_now_iso()
        self._actions.clear()
        self._screenshot_hashes.clear()
        return self._current_trajectory_id

    def record_step(
        self,
        frame: ScreenFrame,
        action: GUIActionRequest,
        receipt: GUIActionReceipt,
    ) -> None:
        """Records an individual execution step within the active trajectory."""
        self._screenshot_hashes.append(frame.frame_hash)
        self._actions.append(
            {
                "timestamp": time.time(),
                "action": action.model_dump(),
                "receipt": receipt.model_dump(),
            }
        )

    def finalize_trajectory(
        self,
        verification_passed: bool,
        verified_speedup_ratio: float = 1.0,
    ) -> ExperienceTrajectoryRecord:
        """Finalizes the recording session into an immutable ExperienceTrajectoryRecord."""
        record = ExperienceTrajectoryRecord(
            trajectory_id=self._current_trajectory_id or f"traj_{uuid.uuid4().hex[:12]}",
            task_id=self._task_id or "task_default",
            employee_id=self._employee_id or "emp_default",
            application_name=self._app_name,
            application_version=self._app_version,
            screen_resolution=self._resolution,
            start_time=self._start_time or utc_now_iso(),
            end_time=utc_now_iso(),
            total_actions=len(self._actions),
            actions=list(self._actions),
            screenshot_hashes=list(self._screenshot_hashes),
            verification_passed=verification_passed,
            verified_speedup_ratio=verified_speedup_ratio,
        )
        logger.info(
            "Experience trajectory finalized",
            trajectory_id=record.trajectory_id,
            total_actions=record.total_actions,
            verification_passed=record.verification_passed,
        )
        return record

