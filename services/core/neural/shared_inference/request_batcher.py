from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, Optional

from neural.logger import get_logger

logger = get_logger("ancos.shared_inference.batcher")


class SharedRequestBatcher:
    """
    Adaptive Micro-Batcher for Shared Neural and LLM Inference.
    Batches concurrent multi-employee requests over a tight millisecond window
    to maximize GPU/CPU throughput without violating context boundaries.
    """

    def __init__(
        self,
        batch_window_ms: int = 10,
        max_batch_size: int = 8,
    ) -> None:
        self.batch_window_ms = batch_window_ms
        self.max_batch_size = max_batch_size
        self._pending: list[tuple[Any, asyncio.Future[Any]]] = []
        self._flush_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()

    async def enqueue_and_wait(
        self,
        payload: Any,
        processor_fn: Callable[[list[Any]], Coroutine[Any, Any, list[Any]]],
    ) -> Any:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()

        async with self._lock:
            self._pending.append((payload, future))
            if len(self._pending) >= self.max_batch_size:
                # Immediate flush
                batch = list(self._pending)
                self._pending.clear()
                if self._flush_task and not self._flush_task.done():
                    self._flush_task.cancel()
                asyncio.create_task(self._process_batch(batch, processor_fn))
            elif self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush(processor_fn))

        return await future

    async def _delayed_flush(
        self,
        processor_fn: Callable[[list[Any]], Coroutine[Any, Any, list[Any]]],
    ) -> None:
        await asyncio.sleep(self.batch_window_ms / 1000.0)
        async with self._lock:
            if not self._pending:
                return
            batch = list(self._pending)
            self._pending.clear()
        await self._process_batch(batch, processor_fn)

    async def _process_batch(
        self,
        batch: list[tuple[Any, asyncio.Future[Any]]],
        processor_fn: Callable[[list[Any]], Coroutine[Any, Any, list[Any]]],
    ) -> None:
        payloads = [p for p, _ in batch]
        futures = [f for _, f in batch]

        try:
            results = await processor_fn(payloads)
            for fut, res in zip(futures, results):
                if not fut.done():
                    fut.set_result(res)
        except Exception as e:
            for fut in futures:
                if not fut.done():
                    fut.set_exception(e)

