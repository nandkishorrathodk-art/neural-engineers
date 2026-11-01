from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

from neural.decision_intelligence.schemas.choice import ChoiceQuestion, ChoiceResult
from neural.decision_intelligence.schemas.noul import NoulQuestion, NoulResult
from neural.decision_intelligence.schemas.score import ScoreQuestion, ScoreResult
from neural.decision_intelligence.system_one.laya_adapter import LayaAdapter
from neural.exceptions import BatchManagerError
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.system_one.batch_manager")

TQuestion = TypeVar("TQuestion")
TResult = TypeVar("TResult")


@dataclass
class QueuedRequest(Generic[TQuestion, TResult]):
    question: TQuestion
    future: asyncio.Future[TResult]


class MicroBatchManager:
    """
    Coalesces asynchronous System 1 decision requests across multiple workers
    over a dynamic micro-batch collection window (default: 10ms, max batch: 32).
    """

    def __init__(
        self,
        adapter: LayaAdapter,
        batch_window_ms: int = 10,
        max_batch_size: int = 32,
    ) -> None:
        self.adapter = adapter
        self.batch_window_ms = batch_window_ms
        self.max_batch_size = max_batch_size

        self._choice_queue: list[QueuedRequest[ChoiceQuestion, ChoiceResult]] = []
        self._score_queue: list[QueuedRequest[ScoreQuestion, ScoreResult]] = []
        self._noul_queue: list[QueuedRequest[NoulQuestion, NoulResult]] = []

        self._lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._flush_event = asyncio.Event()

    def _ensure_running(self) -> None:
        loop = asyncio.get_running_loop()
        needs_restart = (
            not self._running
            or self._worker_task is None
            or self._worker_task.done()
            or getattr(self._worker_task, "get_loop", lambda: loop)() is not loop
        )
        if needs_restart:
            self._lock = asyncio.Lock()
            self._flush_event = asyncio.Event()
            self._running = True
            self._worker_task = asyncio.create_task(self._batch_loop())

    async def start(self) -> None:
        self._ensure_running()
        logger.info("Started MicroBatchManager", window_ms=self.batch_window_ms, max_batch=self.max_batch_size)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._flush_event.set()
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        # Process remaining
        await self._flush_all()
        logger.info("Stopped MicroBatchManager")

    async def enqueue_choice(self, question: ChoiceQuestion) -> ChoiceResult:
        self._ensure_running()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ChoiceResult] = loop.create_future()
        async with self._lock:
            self._choice_queue.append(QueuedRequest(question=question, future=future))
            if len(self._choice_queue) >= self.max_batch_size:
                self._flush_event.set()
        return await future

    async def enqueue_score(self, question: ScoreQuestion) -> ScoreResult:
        self._ensure_running()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ScoreResult] = loop.create_future()
        async with self._lock:
            self._score_queue.append(QueuedRequest(question=question, future=future))
            if len(self._score_queue) >= self.max_batch_size:
                self._flush_event.set()
        return await future

    async def enqueue_noul(self, question: NoulQuestion) -> NoulResult:
        self._ensure_running()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[NoulResult] = loop.create_future()
        async with self._lock:
            self._noul_queue.append(QueuedRequest(question=question, future=future))
            if len(self._noul_queue) >= self.max_batch_size:
                self._flush_event.set()
        return await future

    async def _batch_loop(self) -> None:
        while self._running:
            try:
                # Wait for either max batch reached or batch window timeout
                timeout_s = self.batch_window_ms / 1000.0
                try:
                    await asyncio.wait_for(self._flush_event.wait(), timeout=timeout_s)
                except asyncio.TimeoutError:
                    pass
                self._flush_event.clear()
                await self._flush_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in batch loop", error=str(e))
                await asyncio.sleep(0.005)

    async def _flush_all(self) -> None:
        async with self._lock:
            choices_to_process = list(self._choice_queue)
            self._choice_queue.clear()

            scores_to_process = list(self._score_queue)
            self._score_queue.clear()

            nouls_to_process = list(self._noul_queue)
            self._noul_queue.clear()

        # Execute Choice batch
        if choices_to_process:
            try:
                questions = [item.question for item in choices_to_process]
                results = self.adapter.batch_predict_choice(questions)
                if len(results) != len(questions):
                    raise BatchManagerError(
                        f"Choice batch size mismatch: expected {len(questions)}, got {len(results)}"
                    )
                for item, res in zip(choices_to_process, results):
                    if not item.future.done():
                        item.future.set_result(res)
            except Exception as e:
                err = e if isinstance(e, BatchManagerError) else BatchManagerError(f"Choice batch processing failed: {e}")
                for item in choices_to_process:
                    if not item.future.done():
                        item.future.set_exception(err)

        # Execute Score batch
        if scores_to_process:
            try:
                questions = [item.question for item in scores_to_process]
                results = self.adapter.batch_predict_score(questions)
                if len(results) != len(questions):
                    raise BatchManagerError(
                        f"Score batch size mismatch: expected {len(questions)}, got {len(results)}"
                    )
                for item, res in zip(scores_to_process, results):
                    if not item.future.done():
                        item.future.set_result(res)
            except Exception as e:
                err = e if isinstance(e, BatchManagerError) else BatchManagerError(f"Score batch processing failed: {e}")
                for item in scores_to_process:
                    if not item.future.done():
                        item.future.set_exception(err)

        # Execute Noul batch
        if nouls_to_process:
            try:
                questions = [item.question for item in nouls_to_process]
                results = self.adapter.batch_predict_noul(questions)
                if len(results) != len(questions):
                    raise BatchManagerError(
                        f"Noul batch size mismatch: expected {len(questions)}, got {len(results)}"
                    )
                for item, res in zip(nouls_to_process, results):
                    if not item.future.done():
                        item.future.set_result(res)
            except Exception as e:
                err = e if isinstance(e, BatchManagerError) else BatchManagerError(f"Noul batch processing failed: {e}")
                for item in nouls_to_process:
                    if not item.future.done():
                        item.future.set_exception(err)
