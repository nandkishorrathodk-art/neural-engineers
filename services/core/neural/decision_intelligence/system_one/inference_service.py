from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from neural.config import get_settings
from neural.constants import HealthState
from neural.decision_intelligence.schemas.choice import ChoiceQuestion, ChoiceResult
from neural.decision_intelligence.schemas.noul import NoulQuestion, NoulResult
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult
from neural.decision_intelligence.system_one.batch_manager import MicroBatchManager
from neural.decision_intelligence.system_one.contracts import (
    DecisionPrimitive,
    System1DecisionRequest,
    System1DecisionResult,
)
from neural.decision_intelligence.system_one.laya_adapter import LayaAdapter
from neural.decision_intelligence.system_one.model_registry import (
    ModelRegistry,
    get_model_registry,
)
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.system_one.inference_service")


class LayaInferenceService:
    """
    Shared Singleton Inference Service & Concurrency Manager (Section 7.1).
    Coordinates System 1 decisions across workers with concurrency limiting,
    micro-batching, and health monitoring.
    """

    def __init__(
        self,
        checkpoint_name: Optional[str] = None,
        max_concurrency: int = 16,
        batch_window_ms: Optional[int] = None,
        max_batch_size: Optional[int] = None,
        registry: Optional[ModelRegistry] = None,
    ) -> None:
        settings = get_settings()
        self.checkpoint_name = checkpoint_name or settings.LAYA_DEFAULT_CHECKPOINT
        self.registry = registry or get_model_registry()
        self.adapter = LayaAdapter(checkpoint_name=self.checkpoint_name, registry=self.registry)

        window_ms = batch_window_ms or settings.LAYA_BATCH_WINDOW_MS
        batch_size = max_batch_size or settings.LAYA_MAX_BATCH_SIZE
        self.batch_manager = MicroBatchManager(
            adapter=self.adapter,
            batch_window_ms=window_ms,
            max_batch_size=batch_size,
        )

        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.start_time = time.time()
        self._total_requests = 0
        self._total_errors = 0
        self._total_latency_ms = 0.0
        self._active_requests = 0

    async def decide_choice(self, question: ChoiceQuestion, use_batching: bool = True) -> ChoiceResult:
        async with self.semaphore:
            self._active_requests += 1
            try:
                if use_batching:
                    result = await self.batch_manager.enqueue_choice(question)
                else:
                    result = self.adapter.predict_choice(question)
                self._total_requests += 1
                self._total_latency_ms += result.latency_ms
                return result
            except Exception as e:
                self._total_errors += 1
                logger.error("Choice decision error", error=str(e), question_id=question.question_id)
                raise
            finally:
                self._active_requests -= 1

    async def decide_score(self, question: ScoreQuestion, use_batching: bool = True) -> ScoreResult:
        async with self.semaphore:
            self._active_requests += 1
            try:
                if use_batching:
                    result = await self.batch_manager.enqueue_score(question)
                else:
                    result = self.adapter.predict_score(question)
                self._total_requests += 1
                self._total_latency_ms += result.latency_ms
                return result
            except Exception as e:
                self._total_errors += 1
                logger.error("Score decision error", error=str(e), question_id=question.question_id)
                raise
            finally:
                self._active_requests -= 1

    async def decide_noul(self, question: NoulQuestion, use_batching: bool = True) -> NoulResult:
        async with self.semaphore:
            self._active_requests += 1
            try:
                if use_batching:
                    result = await self.batch_manager.enqueue_noul(question)
                else:
                    result = self.adapter.predict_noul(question)
                self._total_requests += 1
                self._total_latency_ms += result.latency_ms
                return result
            except Exception as e:
                self._total_errors += 1
                logger.error("Noul decision error", error=str(e), question_id=question.question_id)
                raise
            finally:
                self._active_requests -= 1

    async def decide(self, request: System1DecisionRequest, use_batching: bool = True) -> System1DecisionResult:
        if request.primitive == DecisionPrimitive.CHOICE:
            if not request.choice_question:
                raise ValueError("choice_question required for CHOICE primitive")
            c_res = await self.decide_choice(request.choice_question, use_batching=use_batching)
            return System1DecisionResult(
                primitive=request.primitive,
                choice_result=c_res,
                latency_ms=c_res.latency_ms,
                model_name=c_res.model_name,
                confidence=c_res.confidence,
                was_escalated=c_res.was_escalated,
            )
        elif request.primitive == DecisionPrimitive.SCORE:
            if not request.score_question:
                raise ValueError("score_question required for SCORE primitive")
            s_res = await self.decide_score(request.score_question, use_batching=use_batching)
            return System1DecisionResult(
                primitive=request.primitive,
                score_result=s_res,
                latency_ms=s_res.latency_ms,
                model_name=s_res.model_name,
                confidence=s_res.confidence,
                was_escalated=s_res.was_escalated,
            )
        elif request.primitive == DecisionPrimitive.NOUL:
            if not request.noul_question:
                raise ValueError("noul_question required for NOUL primitive")
            n_res = await self.decide_noul(request.noul_question, use_batching=use_batching)
            return System1DecisionResult(
                primitive=request.primitive,
                noul_result=n_res,
                latency_ms=n_res.latency_ms,
                model_name=n_res.model_name,
                confidence=n_res.confidence,
                was_escalated=n_res.was_escalated,
            )
        else:
            raise ValueError(f"Unsupported primitive: {request.primitive}")

    def get_health(self) -> dict[str, Any]:
        uptime_s = time.time() - self.start_time
        avg_lat = (self._total_latency_ms / self._total_requests) if self._total_requests > 0 else 0.0
        err_rate = (self._total_errors / self._total_requests) if self._total_requests > 0 else 0.0

        state = HealthState.HEALTHY
        if err_rate > 0.05 or avg_lat > 50.0:
            state = HealthState.DEGRADED
        if err_rate > 0.20:
            state = HealthState.UNHEALTHY

        return {
            "status": state.value,
            "checkpoint": self.checkpoint_name,
            "runtime": self.adapter.runtime.value,
            "uptime_seconds": round(uptime_s, 2),
            "total_requests": self._total_requests,
            "active_requests": self._active_requests,
            "total_errors": self._total_errors,
            "error_rate": round(err_rate, 4),
            "avg_latency_ms": round(avg_lat, 2),
        }

    async def shutdown(self) -> None:
        await self.batch_manager.stop()


_inference_service_instance: Optional[LayaInferenceService] = None


def get_inference_service() -> LayaInferenceService:
    global _inference_service_instance
    if _inference_service_instance is None:
        _inference_service_instance = LayaInferenceService()
    return _inference_service_instance


def reset_inference_service() -> None:
    global _inference_service_instance
    _inference_service_instance = None
