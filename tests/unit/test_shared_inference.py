from __future__ import annotations

import asyncio

import pytest
from neural.constants import TaskPriority
from neural.shared_inference.inference_queue import SharedInferenceQueue
from neural.shared_inference.model_pool import SharedModelPool
from neural.shared_inference.request_batcher import SharedRequestBatcher


def test_shared_model_pool_singleton_reuse():
    pool = SharedModelPool()

    # Request adapter first time
    adapter1 = pool.get_laya_adapter("Laya-Typed-Decisions")
    # Request adapter second time
    adapter2 = pool.get_laya_adapter("Laya-Typed-Decisions")

    # Must be exact same instance in memory (singleton reuse)
    assert adapter1 is adapter2
    assert pool.count_loaded_models() >= 1

    # Shared LLM Gateway singleton
    gw1 = pool.get_llm_gateway("default")
    gw2 = pool.get_llm_gateway("default")
    assert gw1 is gw2


@pytest.mark.asyncio
async def test_shared_request_batcher_micro_batching():
    batcher = SharedRequestBatcher(batch_window_ms=20, max_batch_size=4)
    processed_batches: list[list[str]] = []

    async def mock_processor(payloads: list[str]) -> list[str]:
        processed_batches.append(list(payloads))
        return [f"processed:{p}" for p in payloads]

    # Concurrently enqueue requests from different employees
    results = await asyncio.gather(
        batcher.enqueue_and_wait("req_emp1", mock_processor),
        batcher.enqueue_and_wait("req_emp2", mock_processor),
        batcher.enqueue_and_wait("req_emp3", mock_processor),
    )

    assert results == [
        "processed:req_emp1",
        "processed:req_emp2",
        "processed:req_emp3",
    ]
    # All 3 were micro-batched together into a single batch call
    assert len(processed_batches) == 1
    assert len(processed_batches[0]) == 3


@pytest.mark.asyncio
async def test_shared_request_batcher_max_batch_flush():
    batcher = SharedRequestBatcher(batch_window_ms=1000, max_batch_size=2)
    processed_batches: list[list[int]] = []

    async def mock_processor(payloads: list[int]) -> list[int]:
        processed_batches.append(list(payloads))
        return [x * 2 for x in payloads]

    # Enqueue 2 items (hitting max_batch_size=2, triggering immediate flush without waiting 1000ms)
    t0 = asyncio.get_event_loop().time()
    results = await asyncio.gather(
        batcher.enqueue_and_wait(10, mock_processor),
        batcher.enqueue_and_wait(20, mock_processor),
    )
    t1 = asyncio.get_event_loop().time()

    assert results == [20, 40]
    assert len(processed_batches) == 1
    # Flushed immediately, well below the 1000ms window
    assert (t1 - t0) < 0.5


def test_shared_inference_queue_fair_share_and_priority():
    queue = SharedInferenceQueue(max_concurrent_in_flight=4, max_per_employee_in_flight=2)

    # Enqueue 3 requests from greedy employee A (Low priority)
    queue.enqueue("req-A1", "EMP-A", "T1", "payloadA1", priority=TaskPriority.LOW)
    queue.enqueue("req-A2", "EMP-A", "T2", "payloadA2", priority=TaskPriority.LOW)
    queue.enqueue("req-A3", "EMP-A", "T3", "payloadA3", priority=TaskPriority.LOW)

    # Enqueue 1 request from employee B (Critical priority)
    queue.enqueue("req-B1", "EMP-B", "T4", "payloadB1", priority=TaskPriority.CRITICAL)

    assert queue.size() == 4

    # 1st dispatch: EMP-B should jump ahead due to CRITICAL priority
    r1 = queue.get_next_dispatchable()
    assert r1 is not None
    assert r1.employee_id == "EMP-B"

    # 2nd dispatch: EMP-A's 1st request
    r2 = queue.get_next_dispatchable()
    assert r2 is not None
    assert r2.employee_id == "EMP-A"

    # 3rd dispatch: EMP-A's 2nd request (hits max_per_emp=2 limit)
    r3 = queue.get_next_dispatchable()
    assert r3 is not None
    assert r3.employee_id == "EMP-A"

    # 4th dispatch: EMP-A has 2 in flight (quota exhausted), so EMP-A's 3rd request CANNOT monopolize!
    r4 = queue.get_next_dispatchable()
    assert r4 is None  # Blocked by anti-starvation policy

    # Complete one of EMP-A's requests
    queue.complete_request("EMP-A")

    # Now EMP-A's 3rd request can proceed
    r5 = queue.get_next_dispatchable()
    assert r5 is not None
    assert r5.request_id == "req-A3"


def test_shared_inference_queue_strict_per_employee_quota():
    """Verifies that an employee at or above quota is NEVER dispatched even when queue has only their requests."""
    queue = SharedInferenceQueue(max_concurrent_in_flight=8, max_per_employee_in_flight=1)

    queue.enqueue("req-1", "EMP-SOLO", "T1", "payload1")
    queue.enqueue("req-2", "EMP-SOLO", "T1", "payload2")

    # 1st dispatch succeeds
    r1 = queue.get_next_dispatchable()
    assert r1 is not None
    assert r1.request_id == "req-1"

    # 2nd dispatch MUST be blocked because EMP-SOLO already has 1 in flight (max_per_emp=1)
    r2 = queue.get_next_dispatchable()
    assert r2 is None

    # After completion, 2nd request can be dispatched
    queue.complete_request("EMP-SOLO")
    r3 = queue.get_next_dispatchable()
    assert r3 is not None
    assert r3.request_id == "req-2"

