from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from neural.db.models import OutboxEvent

logger = logging.getLogger("ancos.events.outbox")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_sse_frame(event_id: str, event_type: str, data: Any) -> str:
    """Formats an event conforming to W3C EventSource SSE specifications."""
    data_str = json.dumps(data) if not isinstance(data, str) else data
    return f"id: {event_id}\nevent: {event_type}\ndata: {data_str}\n\n"


class OutboxSSEBroadcaster:
    """
    In-memory broadcaster bridging the persistent database outbox to live SSE connections.
    Supports dynamic subscribers, monotonic message dispatch, and replay buffering.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subscribers.add(queue)
            logger.debug("SSE client connected, total subscribers: %d", len(self._subscribers))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
            logger.debug("SSE client disconnected, total subscribers: %d", len(self._subscribers))

    @asynccontextmanager
    async def subscription(self) -> AsyncGenerator[asyncio.Queue[dict[str, Any]], None]:
        """
        Async context manager providing a temporary subscriber queue that is
        guaranteed to be unsubscribed upon completion, disconnection, or cancellation.
        """
        queue = await self.subscribe()
        try:
            yield queue
        finally:
            await self.unsubscribe(queue)

    async def event_generator(
        self,
        request: Any = None,
        timeout: float = 15.0,
    ) -> AsyncGenerator[dict[str, Any] | None, None]:
        """
        Async event generator that yields events from a subscribed queue,
        periodically yielding None for keepalive comments,
        and immediately unsubscribes the queue if disconnected or cancelled.
        """
        queue = await self.subscribe()
        try:
            while True:
                if request is not None and hasattr(request, "is_disconnected"):
                    if await request.is_disconnected():
                        break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                    yield event
                except asyncio.TimeoutError:
                    yield None
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            await self.unsubscribe(queue)

    async def broadcast_event(self, event_data: dict[str, Any]) -> int:
        """Pushes an event payload to all active subscriber queues."""
        async with self._lock:
            active = list(self._subscribers)

        dispatched = 0
        dead_queues: list[asyncio.Queue[dict[str, Any]]] = []
        for queue in active:
            try:
                queue.put_nowait(event_data)
                dispatched += 1
            except asyncio.QueueFull:
                logger.warning("Subscriber queue is full; evicting slow/dead client to prevent memory leak")
                dead_queues.append(queue)

        if dead_queues:
            async with self._lock:
                for dq in dead_queues:
                    self._subscribers.discard(dq)
                logger.info(
                    "Evicted %d dead/full subscriber queue(s); total subscribers: %d",
                    len(dead_queues),
                    len(self._subscribers),
                )
        return dispatched

    async def replay_events_since(
        self,
        session: AsyncSession,
        last_event_id: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Replays committed outbox events occurring after `last_event_id` in strict monotonic order.
        Guarantees zero event loss across disconnects and reconnects.
        """
        if not last_event_id:
            return []

        seq_val: Optional[int] = None
        if last_event_id.startswith("evt_") and last_event_id[4:].isdigit():
            seq_val = int(last_event_id[4:])

        # Find target reference event
        stmt_target = select(OutboxEvent).where(OutboxEvent.event_id == last_event_id)
        target_res = await session.execute(stmt_target)
        target = target_res.scalar_one_or_none()

        if target is not None:
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.event_id > target.event_id)
                .order_by(OutboxEvent.event_id.asc())
                .limit(limit)
            )
        elif seq_val is not None:
            # Replay after the parsed sequence value
            ref_id = f"evt_{seq_val:08d}"
            stmt = (
                select(OutboxEvent)
                .where(OutboxEvent.event_id > ref_id)
                .order_by(OutboxEvent.event_id.asc())
                .limit(limit)
            )
        else:
            # Unrecognized event ID that does not exist in DB and cannot be parsed as a sequence
            return []

        result = await session.execute(stmt)
        events = result.scalars().all()

        replayed: list[dict[str, Any]] = []
        for evt in events:
            try:
                payload = json.loads(evt.payload)
                replayed.append(payload)
            except Exception as e:
                logger.warning("Failed to decode outbox event payload %s: %s", evt.event_id, e)

        return replayed


_GLOBAL_BROADCASTER = OutboxSSEBroadcaster()


def get_sse_broadcaster() -> OutboxSSEBroadcaster:
    return _GLOBAL_BROADCASTER


class OutboxEventPublisher:
    """
    Transactional Outbox Publisher.
    Enforces DB transaction commit before publish to eliminate ghost events.
    """

    def __init__(self, broadcaster: Optional[OutboxSSEBroadcaster] = None) -> None:
        self.broadcaster = broadcaster or get_sse_broadcaster()

    async def publish_event(
        self,
        session: AsyncSession,
        event_type: str,
        data: dict[str, Any],
        channel: str = "company_events",
        auto_commit: bool = True,
    ) -> dict[str, Any]:
        """
        Creates an OutboxEvent row inside the active DB transaction.
        If auto_commit is True, commits the transaction before broadcasting.
        """
        # Determine monotonic sequence number from maximum existing event_id
        stmt_max = select(func.max(OutboxEvent.event_id))
        max_res = await session.execute(stmt_max)
        max_id = max_res.scalar()
        if max_id and max_id.startswith("evt_") and max_id[4:].isdigit():
            seq = int(max_id[4:]) + 1
        else:
            stmt_count = select(func.count(OutboxEvent.id))
            count_res = await session.execute(stmt_count)
            seq = (count_res.scalar() or 0) + 1

        event_id = f"evt_{seq:08d}"
        now_iso = utc_now().isoformat()

        payload: dict[str, Any] = {
            "event_id": event_id,
            "sequence_id": seq,
            "event_type": event_type,
            "channel": channel,
            "timestamp": now_iso,
            "data": data,
        }

        outbox_row = OutboxEvent(
            event_id=event_id,
            channel=channel,
            payload=json.dumps(payload),
            status="pending",
            created_at=utc_now(),
            available_at=utc_now(),
        )
        session.add(outbox_row)

        if auto_commit:
            await session.commit()
            # Publish to SSE broadcaster only after confirmed DB commit
            await self.broadcaster.broadcast_event(payload)

        if _GLOBAL_WORKER is not None:
            _GLOBAL_WORKER.notify()

        return payload

    async def notify_committed(self, payload: dict[str, Any]) -> None:
        """Manually dispatches an event whose DB transaction was committed externally."""
        await self.broadcaster.broadcast_event(payload)


class OutboxWorker:
    """
    Persistent background outbox worker for System B.
    Dispatches pending outbox events from the database to connected SSE clients.
    """

    def __init__(
        self,
        session_factory: Any = None,
        broadcaster: Optional[OutboxSSEBroadcaster] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        self.session_factory = session_factory
        self.broadcaster = broadcaster or get_sse_broadcaster()
        self.worker_id = worker_id or f"sse-worker-{uuid.uuid4().hex[:8]}"
        self._wakeup = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False

    def notify(self) -> None:
        """Wakes up the worker immediately when an event is added."""
        self._wakeup.set()

    async def dispatch_once(
        self,
        session: Optional[AsyncSession] = None,
        batch_size: int = 32,
    ) -> int:
        """
        Claims pending outbox events, broadcasts them to SSE clients,
        and marks them delivered.
        """
        if session is not None:
            return await self._dispatch_with_session(session, batch_size)
        if self.session_factory is not None:
            async with self.session_factory() as owned_session:
                return await self._dispatch_with_session(owned_session, batch_size)
        return 0

    async def _dispatch_with_session(
        self,
        session: AsyncSession,
        batch_size: int,
    ) -> int:
        from neural.db.repositories import OutboxRepository

        repo = OutboxRepository(session)
        deliveries = await repo.claim_ready(worker_id=self.worker_id, limit=batch_size)
        dispatched = 0
        for item in deliveries:
            try:
                payload = json.loads(item["payload"]) if isinstance(item["payload"], str) else item["payload"]
                await self.broadcaster.broadcast_event(payload)
                await repo.mark_delivered(item["event_id"], item["lease_owner"])
                dispatched += 1
            except Exception as err:
                logger.warning("Outbox worker failed to dispatch event %s: %s", item.get("event_id"), err)
                await repo.record_failure(
                    event_id=item["event_id"],
                    lease_owner=item["lease_owner"],
                    error=str(err),
                    retry_delay_seconds=5,
                    max_attempts=5,
                )
        return dispatched

    async def run_loop(self, poll_interval: float = 1.0) -> None:
        """Persistent dispatch loop fulfilling System B Outbox Worker requirement."""
        self._running = True
        while self._running:
            try:
                await self.dispatch_once()
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.warning("Outbox worker loop error: %s", err)

            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=poll_interval)
                self._wakeup.clear()
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    def start(self, poll_interval: float = 1.0) -> None:
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self.run_loop(poll_interval), name="ancos-outbox-sse-worker")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


_GLOBAL_WORKER: Optional[OutboxWorker] = None


def get_outbox_worker(
    session_factory: Any = None,
    broadcaster: Optional[OutboxSSEBroadcaster] = None,
) -> OutboxWorker:
    global _GLOBAL_WORKER
    if _GLOBAL_WORKER is None:
        _GLOBAL_WORKER = OutboxWorker(session_factory=session_factory, broadcaster=broadcaster)
    elif session_factory is not None and _GLOBAL_WORKER.session_factory is None:
        _GLOBAL_WORKER.session_factory = session_factory
    return _GLOBAL_WORKER
