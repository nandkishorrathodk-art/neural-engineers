from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Optional

from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop
from neural.gui_intelligence.models import GUIActionRequest
from neural.gui_intelligence.perception.screen_capture import ScreenCaptureEngine
from neural.gui_intelligence.skills.skill_compiler import CompiledGUISkill
from neural.gui_intelligence.skills.skill_registry import GUISkillRegistry, get_gui_skill_registry
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.skills.skill_validator")


@dataclass
class SkillValidationResult:
    passed: bool
    skill_id: str
    consecutive_passes: int
    required_passes: int
    score: float
    permutation_results: list[dict]
    certificate_hash: Optional[str] = None


class SkillValidator:
    """
    Held-Out Permutation Test Harness & Skill Validator (Section 7.1 Step 4 & 5).
    Evaluates compiled skills against 3 consecutive held-out layout, theme, and resolution permutations.
    If the skill achieves 100% pass rate across all 3 permutations, it certifies the skill
    and records it in the GUISkillRegistry.
    """

    REQUIRED_CONSECUTIVE_PASSES: int = 3

    def __init__(
        self,
        registry: Optional[GUISkillRegistry] = None,
        capture_engine: Optional[ScreenCaptureEngine] = None,
        execution_loop: Optional[GUIExecutionLoop] = None,
    ) -> None:
        self.registry = registry or get_gui_skill_registry()
        self.capture_engine = capture_engine or ScreenCaptureEngine()
        self.execution_loop = execution_loop or GUIExecutionLoop()

    def validate_skill(
        self,
        skill: CompiledGUISkill,
        employee_id: str = "emp_cert_validator",
    ) -> SkillValidationResult:
        """
        Runs the 3-permutation held-out generalization test suite.
        Permutation 1: Standard 1280x800 resolution with offset window rect (140, 100, 750, 480).
        Permutation 2: Dark mode theme with high-contrast elements.
        Permutation 3: Scaled 1440x900 resolution with shifted layout.
        """
        if not skill.actions:
            return SkillValidationResult(
                passed=False,
                skill_id=skill.skill_id,
                consecutive_passes=0,
                required_passes=self.REQUIRED_CONSECUTIVE_PASSES,
                score=0.0,
                permutation_results=[{"error": "Empty skill cannot be certified"}],
                certificate_hash=None,
            )

        from neural.gui_intelligence.perception.visual_grounding import VisualGrounder
        grounder = VisualGrounder()

        permutations = [
            {
                "name": "ShiftedWindow_1280x800",
                "resolution": (1280, 800),
                "bg_color": (45, 50, 60),
                "win_rect": (150, 110, 750, 480),
            },
            {
                "name": "DarkMode_Theme",
                "resolution": (1280, 800),
                "bg_color": (25, 25, 28),
                "win_rect": (80, 60, 820, 520),
            },
            {
                "name": "ScaledResolution_1440x900",
                "resolution": (1440, 900),
                "bg_color": (50, 55, 65),
                "win_rect": (200, 140, 800, 500),
            },
        ]

        permutation_results = []
        passes = 0

        for perm in permutations:
            w, h = perm["resolution"]
            wx, wy, ww, wh = perm["win_rect"]
            # Render synthetic environment for this permutation with standard controls
            frame = self.capture_engine.render_synthetic_desktop(
                windows=[
                    {
                        "title": skill.application_name,
                        "rect": perm["win_rect"],
                        "is_active": True,
                        "elements": [
                            {"type": "menu", "text": "File", "rect": (wx + 20, wy + 40, 60, 24)},
                            {"type": "menu_item", "text": "Save As...", "rect": (wx + 20, wy + 70, 100, 28)},
                            {"type": "button", "text": "Save", "rect": (wx + 50, wy + 110, 90, 32)},
                            {"type": "button", "text": "Submit", "rect": (wx + 160, wy + 110, 90, 32)},
                            {"type": "text_input", "text": "Input", "rect": (wx + 50, wy + 160, 200, 32), "is_focused": True},
                        ],
                    }
                ],
                background_color=perm["bg_color"],
                resolution=(w, h),
            )

            # Map relative skill actions to current permutation frame
            success = True
            for action in skill.actions:
                coords = None
                # Try grounding relative visual anchor if possible
                anchor_target = None
                for key in ("text='", 'text="', "label='", 'label="'):
                    if key in action.relative_anchor:
                        quote_char = key[-1]
                        s_idx = action.relative_anchor.find(key) + len(key)
                        e_idx = action.relative_anchor.find(quote_char, s_idx)
                        if e_idx != -1:
                            anchor_target = action.relative_anchor[s_idx:e_idx]
                            break

                if anchor_target:
                    grounded = grounder.ground_query(frame, anchor_target)
                    if grounded.is_confident:
                        coords = grounded.coordinates

                if coords is None and action.fallback_offset:
                    rx, ry = action.fallback_offset
                    coords = (int(rx * w), int(ry * h))

                req = GUIActionRequest(
                    action_id=f"val_{action.step_index}_{int(time.time()*1000)}",
                    employee_id=employee_id,
                    action_type=action.action_type,  # type: ignore
                    coordinates=coords,
                    text_content=action.text_content,
                    key_code=action.key_code,
                    confidence=0.95,
                    risk_level="LOW",
                )
                receipt = self.execution_loop.execute_action(req)
                if receipt.status != "SUCCESS":
                    success = False
                    break

            if success:
                passes += 1
                permutation_results.append({"permutation": perm["name"], "status": "PASSED"})
            else:
                permutation_results.append({"permutation": perm["name"], "status": "FAILED"})

        all_passed = passes == self.REQUIRED_CONSECUTIVE_PASSES
        cert_hash = None

        if all_passed:
            self.registry.mark_certified(skill.skill_id)
            payload = {
                "skill_id": skill.skill_id,
                "skill_name": skill.skill_name,
                "passes": passes,
                "timestamp": time.time(),
            }
            cert_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

        return SkillValidationResult(
            passed=all_passed,
            skill_id=skill.skill_id,
            consecutive_passes=passes,
            required_passes=self.REQUIRED_CONSECUTIVE_PASSES,
            score=passes / float(self.REQUIRED_CONSECUTIVE_PASSES),
            permutation_results=permutation_results,
            certificate_hash=cert_hash,
        )
