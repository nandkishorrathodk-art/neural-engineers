from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from neural.constants import DecisionRiskLevel
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.routing.complexity_classifier")


class ComplexityAssessment(BaseModel):
    complexity_score: float = Field(ge=0.0, le=1.0)
    is_eligible_for_system1: bool
    risk_level: str
    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ComplexityClassifier:
    """
    Evaluates cognitive choices to determine eligibility for fast System 1 execution (Section 4 & 5).
    Rejects complex planning, multi-step code synthesis, or high-risk destructive actions.
    """

    HIGH_RISK_KEYWORDS: list[str] = [
        "delete",
        "drop table",
        "truncate",
        "rm -rf",
        "production_deploy",
        "payment",
        "wire_transfer",
        "credential_rotation",
        "private_key",
        "kill_switch",
    ]

    DEEP_REASONING_KEYWORDS: list[str] = [
        "architecture_review",
        "strategic_initiative",
        "root_cause_analysis",
        "synthesize_code",
        "refactor_entire_module",
        "boardroom_deliberation",
        "multi_agent_negotiation",
        "hypothesize",
    ]

    ROUTINE_SYSTEM1_KEYWORDS: list[str] = [
        "route_task",
        "select_candidate",
        "menu_item",
        "button_click",
        "priority_score",
        "urgency_rating",
        "is_blocked",
        "anomaly_check",
        "specialist_pick",
    ]

    def __init__(self, max_context_chars: int = 1500) -> None:
        self.max_context_chars = max_context_chars

    def assess(
        self,
        context: str,
        num_candidates: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> ComplexityAssessment:
        reasons: list[str] = []
        complexity = 0.2  # baseline routine complexity
        meta = metadata or {}

        text_lower = context.lower()

        # 1. Candidate cardinality check
        if num_candidates > 12:
            reasons.append(f"Candidate count ({num_candidates}) exceeds System 1 maximum (12)")
            complexity += 0.5

        # 2. Context length check
        if len(context) > self.max_context_chars:
            reasons.append(f"Context length ({len(context)}) exceeds fast decision window ({self.max_context_chars})")
            complexity += 0.3

        # 3. High-risk security check
        matched_risks = [k for k in self.HIGH_RISK_KEYWORDS if k in text_lower]
        if matched_risks or meta.get("destructive", False):
            reasons.append(f"High-risk destructive triggers detected: {matched_risks}")
            complexity = 1.0
            return ComplexityAssessment(
                complexity_score=1.0,
                is_eligible_for_system1=False,
                risk_level=DecisionRiskLevel.CRITICAL.value,
                reasons=reasons,
                confidence=0.99,
            )

        # 4. Deep reasoning check
        matched_deep = [k for k in self.DEEP_REASONING_KEYWORDS if k in text_lower]
        if matched_deep:
            reasons.append(f"Deep reasoning keywords detected: {matched_deep}")
            complexity += 0.4

        # 5. Routine cues
        matched_routine = [k for k in self.ROUTINE_SYSTEM1_KEYWORDS if k in text_lower]
        if matched_routine:
            complexity -= 0.15

        # Clamp complexity score
        final_score = round(max(0.05, min(0.99, complexity)), 2)

        # Determine risk level
        if final_score >= 0.8:
            risk = DecisionRiskLevel.HIGH.value
        elif final_score >= 0.5:
            risk = DecisionRiskLevel.MEDIUM.value
        else:
            risk = DecisionRiskLevel.LOW.value

        is_eligible = final_score <= 0.60 and num_candidates <= 12
        if not is_eligible and not reasons:
            reasons.append(f"Complexity score ({final_score}) exceeds System 1 threshold (0.60)")

        return ComplexityAssessment(
            complexity_score=final_score,
            is_eligible_for_system1=is_eligible,
            risk_level=risk,
            reasons=reasons,
            confidence=0.92,
        )
