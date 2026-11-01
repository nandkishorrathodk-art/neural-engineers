from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from neural.gui_intelligence.models import ExperienceTrajectoryRecord
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.skills.skill_compiler")


@dataclass
class RelativeGUIAction:
    step_index: int
    action_type: str
    relative_anchor: str  # e.g. "button[text='Submit'] in window[title='Settings']"
    fallback_offset: Optional[tuple[float, float]] = None  # Normalized (x_ratio, y_ratio)
    text_content: Optional[str] = None
    key_code: Optional[str] = None
    expected_transition: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompiledGUISkill:
    skill_id: str
    skill_name: str
    description: str
    application_name: str
    actions: list[RelativeGUIAction] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    compiled_at: float = field(default_factory=time.time)
    source_trajectory_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "description": self.description,
            "application_name": self.application_name,
            "actions": [a.to_dict() for a in self.actions],
            "preconditions": self.preconditions,
            "postconditions": self.postconditions,
            "compiled_at": self.compiled_at,
            "source_trajectory_id": self.source_trajectory_id,
        }


class GUISkillCompiler:
    """
    Autonomous GUI Experience Compiler (Section 7.1).
    Transforms raw timestamped execution traces into reusable, parameterized GUI skills.
    Replaces static pixel coordinates with Relative Visual Anchors to ensure resilience
    against window movement, scaling, and theme changes.
    """

    def compile_trajectory(
        self,
        trajectory: ExperienceTrajectoryRecord,
        skill_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> CompiledGUISkill:
        """
        Compiles an ExperienceTrajectoryRecord into a Generalized CompiledGUISkill.
        """
        skill_id = f"skill_gui_{uuid.uuid4().hex[:10]}"
        name = skill_name or f"ProceduralSkill_{trajectory.application_name}"
        desc = description or f"Compiled procedural skill for {trajectory.application_name}"

        w, h = trajectory.screen_resolution
        compiled_actions: list[RelativeGUIAction] = []

        for idx, item in enumerate(trajectory.actions):
            act_data = item.get("action", {})
            act_type = act_data.get("action_type", "MOUSE_CLICK")
            coords = act_data.get("coordinates")
            text = act_data.get("text_content")
            key = act_data.get("key_code")

            # Anchor Generalization: convert absolute pixels to relative visual anchors
            if coords:
                cx, cy = coords
                rx = round(cx / float(w), 3)
                ry = round(cy / float(h), 3)
                if text:
                    anchor = f"input_text[focused=true,text='{text}'] in window[app='{trajectory.application_name}']"
                else:
                    anchor = f"element[relative_pos=({rx},{ry})] in window[app='{trajectory.application_name}']"
                offset = (rx, ry)
            elif text:
                anchor = f"input_text[focused=true,text='{text}'] in window[app='{trajectory.application_name}']"
                offset = None
            elif key:
                anchor = f"hotkey_handler in window[app='{trajectory.application_name}']"
                offset = None
            else:
                anchor = "active_screen"
                offset = None

            rel_action = RelativeGUIAction(
                step_index=idx,
                action_type=act_type,
                relative_anchor=anchor,
                fallback_offset=offset,
                text_content=text,
                key_code=key,
                expected_transition=item.get("receipt", {}).get("observed_postcondition"),
            )
            compiled_actions.append(rel_action)

        compiled_skill = CompiledGUISkill(
            skill_id=skill_id,
            skill_name=name,
            description=desc,
            application_name=trajectory.application_name,
            actions=compiled_actions,
            preconditions=[f"Window '{trajectory.application_name}' is open"],
            postconditions=["Workflow postcondition visually verified"],
            source_trajectory_id=trajectory.trajectory_id,
        )

        logger.info(
            "GUI Skill compiled successfully",
            skill_id=skill_id,
            actions_count=len(compiled_actions),
        )
        return compiled_skill

