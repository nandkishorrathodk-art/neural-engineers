from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class NoulQuestion(BaseModel):
    question_id: str = Field(default_factory=lambda: f"q_noul_{uuid.uuid4().hex[:8]}")
    proposition: str
    context: str = ""
    prior_prob: float = Field(default=0.5, ge=0.0, le=1.0)
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    actor_id: str = "boss"
    task_id: Optional[str] = None
    domain: str = "general"


class NoulResult(BaseModel):
    question_id: str
    is_true: bool
    probability_true: float = Field(ge=0.0, le=1.0)
    calibrated_prob: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    latency_ms: float
    model_name: str
    was_escalated: bool = False
