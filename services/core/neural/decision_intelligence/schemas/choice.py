from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class ChoiceCandidate(BaseModel):
    id: str
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: Optional[float] = None


class ChoiceQuestion(BaseModel):
    question_id: str = Field(default_factory=lambda: f"q_choice_{uuid.uuid4().hex[:8]}")
    context: str
    candidates: list[ChoiceCandidate]
    actor_id: str = "boss"
    task_id: Optional[str] = None
    domain: str = "general"

    @field_validator("candidates")
    @classmethod
    def validate_candidates(cls, v: list[ChoiceCandidate]) -> list[ChoiceCandidate]:
        if len(v) < 2:
            raise ValueError("Choice questions require at least 2 candidates")
        if len(v) > 12:
            raise ValueError(f"Choice questions strictly capped at 12 candidates (received {len(v)})")
        ids = [c.id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Candidate IDs must be unique")
        return v


class ChoiceResult(BaseModel):
    question_id: str
    selected_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float]
    latency_ms: float
    model_name: str
    was_escalated: bool = False
    reasoning_hint: Optional[str] = None
