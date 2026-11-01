from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from neural.decision_intelligence.schemas.choice import ChoiceQuestion, ChoiceResult
from neural.decision_intelligence.schemas.noul import NoulQuestion, NoulResult
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult


class DecisionPrimitive(str, Enum):
    CHOICE = "CHOICE"
    SCORE = "SCORE"
    NOUL = "NOUL"


class System1DecisionRequest(BaseModel):
    primitive: DecisionPrimitive
    choice_question: Optional[ChoiceQuestion] = None
    score_question: Optional[ScoreQuestion] = None
    noul_question: Optional[NoulQuestion] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class System1DecisionResult(BaseModel):
    primitive: DecisionPrimitive
    choice_result: Optional[ChoiceResult] = None
    score_result: Optional[ScoreResult] = None
    noul_result: Optional[NoulResult] = None
    latency_ms: float
    model_name: str
    confidence: float
    was_escalated: bool = False
