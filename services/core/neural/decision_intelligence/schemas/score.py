from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ScoreQuestion(BaseModel):
    question_id: str = Field(default_factory=lambda: f"q_score_{uuid.uuid4().hex[:8]}")
    proposition: str
    context: str = ""
    scale_min: int = 1
    scale_max: int = 5
    actor_id: str = "boss"
    task_id: Optional[str] = None
    domain: str = "general"

    @field_validator("scale_max")
    @classmethod
    def validate_scale(cls, v: int, info) -> int:
        scale_min = info.data.get("scale_min", 1)
        if v <= scale_min:
            raise ValueError(f"scale_max ({v}) must be greater than scale_min ({scale_min})")
        return v


class ScoreResult(BaseModel):
    question_id: str
    score: int
    probabilities: dict[int, float]
    confidence: float = Field(ge=0.0, le=1.0)
    latency_ms: float
    model_name: str
    was_escalated: bool = False
