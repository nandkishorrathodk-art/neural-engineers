from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import get_decision_record_repo
from neural.db.repositories import DecisionRecordRepository
from neural.decision_intelligence.evaluation.benchmark_runner import (
    BenchmarkReport,
    BenchmarkRunner,
)
from neural.decision_intelligence.governance.promotion import (
    PromotionAssessment,
    PromotionManager,
)
from neural.decision_intelligence.governance.rollback import (
    RollbackEvent,
    get_rollback_engine,
)
from neural.decision_intelligence.governance.shadow_mode import (
    get_shadow_mode_manager,
)
from neural.decision_intelligence.observability import (
    PerformanceReporter,
    get_decision_audit_logger,
    get_model_health_monitor,
)
from neural.decision_intelligence.routing.decision_router import (
    IntelligentDecisionRouter,
    get_decision_router,
)
from neural.decision_intelligence.schemas.choice import ChoiceQuestion, ChoiceResult
from neural.decision_intelligence.schemas.noul import NoulQuestion, NoulResult
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult
from neural.decision_intelligence.system_one.contracts import (
    DecisionPrimitive,
    System1DecisionRequest,
    System1DecisionResult,
)
from neural.exceptions import EmergencyStopTriggered
from neural.logger import get_logger

logger = get_logger("ancos.api.decision_intelligence")

router = APIRouter(prefix="/decision-intelligence", tags=["decision-intelligence"])


@router.post("/choice", response_model=ChoiceResult)
async def decide_choice(
    question: ChoiceQuestion,
    repo: DecisionRecordRepository = Depends(get_decision_record_repo),
    decision_router: IntelligentDecisionRouter = Depends(get_decision_router),
) -> ChoiceResult:
    try:
        result = await decision_router.route_choice(question)
        # Log to audit chain
        audit = get_decision_audit_logger()
        audit.log_decision(
            decision_id=result.question_id,
            decision_type="CHOICE",
            output_choice=result.selected_id,
            confidence=result.confidence,
        )
        # Record health call
        health = get_model_health_monitor()
        health.record_call(result.latency_ms, is_error=False)

        # Persist to database
        try:
            in_shadow = get_shadow_mode_manager().is_in_shadow_mode(question.domain)
            await repo.create(
                decision_id=result.question_id,
                actor_id=question.actor_id,
                task_id=question.task_id,
                decision_type="CHOICE",
                model_checkpoint=result.model_name,
                input_payload=question.model_dump(),
                output_choice=result.selected_id,
                probabilities=result.probabilities,
                confidence=result.confidence,
                calibrated_prob=result.confidence,
                latency_ms=result.latency_ms,
                was_escalated=result.was_escalated,
                shadow_mode=in_shadow,
            )
        except Exception as e:
            logger.warning("Could not persist decision record to DB", error=str(e))

        return result
    except EmergencyStopTriggered as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        health = get_model_health_monitor()
        health.record_call(0.0, is_error=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/score", response_model=ScoreResult)
async def decide_score(
    question: ScoreQuestion,
    repo: DecisionRecordRepository = Depends(get_decision_record_repo),
    decision_router: IntelligentDecisionRouter = Depends(get_decision_router),
) -> ScoreResult:
    try:
        result = await decision_router.route_score(question)
        audit = get_decision_audit_logger()
        audit.log_decision(
            decision_id=result.question_id,
            decision_type="SCORE",
            output_choice=str(result.score),
            confidence=result.confidence,
        )
        health = get_model_health_monitor()
        health.record_call(result.latency_ms, is_error=False)

        try:
            in_shadow = get_shadow_mode_manager().is_in_shadow_mode(question.domain)
            await repo.create(
                decision_id=result.question_id,
                actor_id=question.actor_id,
                task_id=question.task_id,
                decision_type="SCORE",
                model_checkpoint=result.model_name,
                input_payload=question.model_dump(),
                output_choice=str(result.score),
                probabilities={str(k): v for k, v in result.probabilities.items()},
                confidence=result.confidence,
                calibrated_prob=result.confidence,
                latency_ms=result.latency_ms,
                was_escalated=result.was_escalated,
                shadow_mode=in_shadow,
            )
        except Exception as e:
            logger.warning("Could not persist decision record to DB", error=str(e))

        return result
    except EmergencyStopTriggered as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        health = get_model_health_monitor()
        health.record_call(0.0, is_error=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/noul", response_model=NoulResult)
async def decide_noul(
    question: NoulQuestion,
    repo: DecisionRecordRepository = Depends(get_decision_record_repo),
    decision_router: IntelligentDecisionRouter = Depends(get_decision_router),
) -> NoulResult:
    try:
        result = await decision_router.route_noul(question)
        audit = get_decision_audit_logger()
        audit.log_decision(
            decision_id=result.question_id,
            decision_type="NOUL",
            output_choice=str(result.is_true),
            confidence=result.confidence,
        )
        health = get_model_health_monitor()
        health.record_call(result.latency_ms, is_error=False)

        try:
            in_shadow = get_shadow_mode_manager().is_in_shadow_mode(question.domain)
            await repo.create(
                decision_id=result.question_id,
                actor_id=question.actor_id,
                task_id=question.task_id,
                decision_type="NOUL",
                model_checkpoint=result.model_name,
                input_payload=question.model_dump(),
                output_choice=str(result.is_true),
                probabilities={"true": result.probability_true, "false": 1.0 - result.probability_true},
                confidence=result.confidence,
                calibrated_prob=result.calibrated_prob,
                latency_ms=result.latency_ms,
                was_escalated=result.was_escalated,
                shadow_mode=in_shadow,
            )
        except Exception as e:
            logger.warning("Could not persist decision record to DB", error=str(e))

        return result
    except EmergencyStopTriggered as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        health = get_model_health_monitor()
        health.record_call(0.0, is_error=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/route", response_model=System1DecisionResult)
async def route_decision(
    request: System1DecisionRequest,
    decision_router: IntelligentDecisionRouter = Depends(get_decision_router),
) -> System1DecisionResult:
    try:
        if request.primitive == DecisionPrimitive.CHOICE:
            if not request.choice_question:
                raise HTTPException(status_code=422, detail="choice_question is required")
            res = await decision_router.route_choice(request.choice_question)
            audit = get_decision_audit_logger()
            audit.log_decision(res.question_id, "CHOICE", res.selected_id, res.confidence)
            get_model_health_monitor().record_call(res.latency_ms, is_error=False)
            return System1DecisionResult(
                primitive=request.primitive,
                choice_result=res,
                latency_ms=res.latency_ms,
                model_name=res.model_name,
                confidence=res.confidence,
                was_escalated=res.was_escalated,
            )
        elif request.primitive == DecisionPrimitive.SCORE:
            if not request.score_question:
                raise HTTPException(status_code=422, detail="score_question is required")
            res_s = await decision_router.route_score(request.score_question)
            audit = get_decision_audit_logger()
            audit.log_decision(res_s.question_id, "SCORE", str(res_s.score), res_s.confidence)
            get_model_health_monitor().record_call(res_s.latency_ms, is_error=False)
            return System1DecisionResult(
                primitive=request.primitive,
                score_result=res_s,
                latency_ms=res_s.latency_ms,
                model_name=res_s.model_name,
                confidence=res_s.confidence,
                was_escalated=res_s.was_escalated,
            )
        elif request.primitive == DecisionPrimitive.NOUL:
            if not request.noul_question:
                raise HTTPException(status_code=422, detail="noul_question is required")
            res_n = await decision_router.route_noul(request.noul_question)
            audit = get_decision_audit_logger()
            audit.log_decision(res_n.question_id, "NOUL", str(res_n.is_true), res_n.confidence)
            get_model_health_monitor().record_call(res_n.latency_ms, is_error=False)
            return System1DecisionResult(
                primitive=request.primitive,
                noul_result=res_n,
                latency_ms=res_n.latency_ms,
                model_name=res_n.model_name,
                confidence=res_n.confidence,
                was_escalated=res_n.was_escalated,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported primitive {request.primitive}")
    except EmergencyStopTriggered as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/health")
async def get_health() -> dict[str, Any]:
    reporter = PerformanceReporter()
    return reporter.generate_report()


@router.get("/audit")
async def get_audit_trail(limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    audit = get_decision_audit_logger()
    is_valid, broken_idx = audit.verify_integrity()
    entries = audit.get_entries(limit=limit)
    return {
        "integrity_verified": is_valid,
        "broken_at_index": broken_idx,
        "entry_count": len(entries),
        "entries": [e.model_dump() for e in entries],
    }


@router.get("/metrics")
async def get_performance_metrics() -> dict[str, Any]:
    reporter = PerformanceReporter()
    return reporter.generate_report()


@router.post("/benchmark", response_model=BenchmarkReport)
async def run_benchmark(
    decision_router: IntelligentDecisionRouter = Depends(get_decision_router),
) -> BenchmarkReport:
    runner = BenchmarkRunner(router=decision_router)
    return await runner.run_benchmark()


@router.post("/promotion/check", response_model=PromotionAssessment)
async def check_promotion(
    category: str,
    auto_promote: bool = False,
    repo: DecisionRecordRepository = Depends(get_decision_record_repo),
) -> PromotionAssessment:
    mgr = PromotionManager(shadow_manager=get_shadow_mode_manager())
    recent = await repo.list_recent(limit=500)

    category_lower = category.lower()
    preds: list[str | int] = []
    truths: list[str | int] = []
    confs: list[float] = []
    lats: list[float] = []

    for r in recent:
        if not r.actual_outcome:
            continue
        domain = (r.input_payload.get("domain", "") if r.input_payload else "").lower()
        dec_type = (r.decision_type or "").lower()
        if (
            category_lower in (domain, dec_type, "all", "general")
            or (category_lower == "gui_selection" and domain == "gui")
        ):
            preds.append(r.output_choice or "")
            truths.append(r.actual_outcome)
            confs.append(r.confidence)
            lats.append(r.latency_ms)

    return mgr.evaluate_promotion(
        category=category,
        predictions=preds,
        ground_truths=truths,
        confidences=confs,
        latencies_ms=lats,
        auto_promote=auto_promote,
    )


@router.post("/rollback", response_model=RollbackEvent)
async def trigger_rollback(
    category: str,
    reason: str = "Manual operator rollback",
) -> RollbackEvent:
    engine = get_rollback_engine()
    return engine.execute_rollback(category=category, reason=reason)
