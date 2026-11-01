from __future__ import annotations

from typing import Optional

from neural.decision_intelligence.calibration.thresholds import (
    ThresholdManager,
    get_threshold_manager,
)
from neural.decision_intelligence.routing.candidate_selector import CandidateSelector
from neural.decision_intelligence.routing.complexity_classifier import ComplexityClassifier
from neural.decision_intelligence.routing.escalation import EscalationController
from neural.decision_intelligence.schemas.choice import ChoiceQuestion, ChoiceResult
from neural.decision_intelligence.schemas.noul import NoulQuestion, NoulResult
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult
from neural.decision_intelligence.system_one.contracts import DecisionPrimitive
from neural.decision_intelligence.system_one.inference_service import (
    LayaInferenceService,
    get_inference_service,
)
from neural.exceptions import EmergencyStopTriggered
from neural.logger import get_logger
from neural.security.kill_switch import is_kill_switch_active

logger = get_logger("ancos.decision_intelligence.routing.decision_router")


class IntelligentDecisionRouter:
    """
    Central Intelligent Decision Router (Section 4 & 5).
    Directs cognitive choices between Laya System 1 (<40ms) and Deep Brain System 2.
    """

    def __init__(
        self,
        inference_service: Optional[LayaInferenceService] = None,
        complexity_classifier: Optional[ComplexityClassifier] = None,
        candidate_selector: Optional[CandidateSelector] = None,
        threshold_manager: Optional[ThresholdManager] = None,
        escalation_controller: Optional[EscalationController] = None,
    ) -> None:
        self.inference_service = inference_service or get_inference_service()
        self.complexity_classifier = complexity_classifier or ComplexityClassifier()
        self.candidate_selector = candidate_selector or CandidateSelector()
        self.threshold_manager = threshold_manager or get_threshold_manager()
        self.escalation_controller = escalation_controller or EscalationController()

    def _verify_kill_switch(self) -> None:
        if is_kill_switch_active():
            raise EmergencyStopTriggered("Global Emergency Kill Switch is active. All decisions aborted.")

    async def route_choice(
        self,
        question: ChoiceQuestion,
        force_system2: bool = False,
        shadow_mode: bool = False,
    ) -> ChoiceResult:
        self._verify_kill_switch()

        # 1. Candidate preparation & bounding (2 <= K <= 12)
        bounded_candidates = self.candidate_selector.prepare_candidates(
            question.candidates, context=question.context
        )
        prepared_question = question.model_copy(update={"candidates": bounded_candidates})

        # 2. Complexity classification
        assessment = self.complexity_classifier.assess(
            context=prepared_question.context,
            num_candidates=len(prepared_question.candidates),
        )

        # 3. Escalation check
        if force_system2 or not assessment.is_eligible_for_system1:
            reason = assessment.reasons[0] if assessment.reasons else "Manual force System 2"
            return await self.escalation_controller.escalate_choice(prepared_question, reason=reason)

        # 4. System 1 inference
        s1_result = await self.inference_service.decide_choice(prepared_question)

        # 5. Calibrated threshold check
        threshold = self.threshold_manager.get_threshold(
            prepared_question.domain, DecisionPrimitive.CHOICE
        )

        if s1_result.confidence < threshold:
            reason = f"Confidence {s1_result.confidence:.2f} below calibrated threshold {threshold:.2f}"
            return await self.escalation_controller.escalate_choice(
                prepared_question, reason=reason, intermediate_result=s1_result
            )

        return s1_result

    async def route_score(
        self,
        question: ScoreQuestion,
        force_system2: bool = False,
        shadow_mode: bool = False,
    ) -> ScoreResult:
        self._verify_kill_switch()

        assessment = self.complexity_classifier.assess(
            context=f"{question.proposition} {question.context}",
            num_candidates=question.scale_max - question.scale_min + 1,
        )

        if force_system2 or not assessment.is_eligible_for_system1:
            reason = assessment.reasons[0] if assessment.reasons else "Manual force System 2"
            return await self.escalation_controller.escalate_score(question, reason=reason)

        s1_result = await self.inference_service.decide_score(question)

        threshold = self.threshold_manager.get_threshold(
            question.domain, DecisionPrimitive.SCORE
        )

        if s1_result.confidence < threshold:
            reason = f"Score confidence {s1_result.confidence:.2f} below threshold {threshold:.2f}"
            return await self.escalation_controller.escalate_score(
                question, reason=reason, intermediate_result=s1_result
            )

        return s1_result

    async def route_noul(
        self,
        question: NoulQuestion,
        force_system2: bool = False,
        shadow_mode: bool = False,
    ) -> NoulResult:
        self._verify_kill_switch()

        assessment = self.complexity_classifier.assess(
            context=f"{question.proposition} {question.context}",
            num_candidates=2,
        )

        if force_system2 or not assessment.is_eligible_for_system1:
            reason = assessment.reasons[0] if assessment.reasons else "Manual force System 2"
            return await self.escalation_controller.escalate_noul(question, reason=reason)

        s1_result = await self.inference_service.decide_noul(question)

        threshold = self.threshold_manager.get_threshold(
            question.domain, DecisionPrimitive.NOUL
        )

        if s1_result.confidence < threshold:
            reason = f"Noul confidence {s1_result.confidence:.2f} below threshold {threshold:.2f}"
            return await self.escalation_controller.escalate_noul(
                question, reason=reason, intermediate_result=s1_result
            )

        return s1_result


_router_instance: Optional[IntelligentDecisionRouter] = None


def get_decision_router() -> IntelligentDecisionRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = IntelligentDecisionRouter()
    return _router_instance


def reset_decision_router() -> None:
    global _router_instance
    _router_instance = None
