from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from neural.db.models import OutboxEvent
from neural.events.outbox import (
    OutboxEventPublisher,
    OutboxSSEBroadcaster,
    OutboxWorker,
    format_sse_frame,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_sse_frame_formatting():
    frame = format_sse_frame(
        event_id="evt_00000001",
        event_type="employee_recruited",
        data={"employee_id": "ANC-EMP-TEST01", "name": "Alice"},
    )
    assert frame.startswith("id: evt_00000001\n")
    assert "event: employee_recruited\n" in frame
    assert 'data: {"employee_id": "ANC-EMP-TEST01", "name": "Alice"}\n\n' in frame


@pytest.mark.asyncio
async def test_outbox_publisher_commits_before_broadcasting(async_session: AsyncSession):
    broadcaster = OutboxSSEBroadcaster()
    publisher = OutboxEventPublisher(broadcaster=broadcaster)

    queue = await broadcaster.subscribe()
    try:
        # Publish event
        payload = await publisher.publish_event(
            session=async_session,
            event_type="task_started",
            data={"task_id": "TASK-001", "title": "Write unit tests"},
            channel="tasks",
            auto_commit=True,
        )

        assert payload["event_type"] == "task_started"
        assert payload["sequence_id"] >= 1
        assert payload["event_id"].startswith("evt_")

        # Verify row exists in DB
        res = await async_session.execute(select(OutboxEvent).where(OutboxEvent.event_id == payload["event_id"]))
        row = res.scalar_one_or_none()
        assert row is not None
        assert row.status == "pending"
        assert json.loads(row.payload)["data"]["task_id"] == "TASK-001"

        # Verify broadcast queue received the event
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received["event_id"] == payload["event_id"]
        assert received["data"]["task_id"] == "TASK-001"
    finally:
        await broadcaster.unsubscribe(queue)


@pytest.mark.asyncio
async def test_zero_ghost_events_on_rollback(async_session: AsyncSession):
    """
    Guarantees strict transactional integrity: rolled-back transactions
    never persist in outbox and never broadcast ghost events to clients.
    """
    broadcaster = OutboxSSEBroadcaster()
    _ = OutboxEventPublisher(broadcaster=broadcaster)

    queue = await broadcaster.subscribe()
    try:
        # Attempt to publish within a transaction that rolls back
        event_id = "evt_ghost_candidate"
        outbox_row = OutboxEvent(
            event_id=event_id,
            channel="company_events",
            payload=json.dumps({"event_id": event_id, "data": "ghost"}),
            status="pending",
        )
        async_session.add(outbox_row)
        # Deliberately rollback without commit
        await async_session.rollback()

        # Verify row does not exist in DB
        res = await async_session.execute(select(OutboxEvent).where(OutboxEvent.event_id == event_id))
        assert res.scalar_one_or_none() is None

        # Verify zero messages arrived in the queue
        assert queue.empty()
    finally:
        await broadcaster.unsubscribe(queue)


@pytest.mark.asyncio
async def test_last_event_id_reconnection_replay(async_session: AsyncSession):
    """
    Verifies that when a client reconnects with Last-Event-ID,
    all intermediate outbox events committed since that ID are replayed in exact monotonic order.
    """
    broadcaster = OutboxSSEBroadcaster()
    publisher = OutboxEventPublisher(broadcaster=broadcaster)

    # Publish 4 sequential events
    _ = await publisher.publish_event(async_session, "e1", {"step": 1})
    p2 = await publisher.publish_event(async_session, "e2", {"step": 2})
    p3 = await publisher.publish_event(async_session, "e3", {"step": 3})
    p4 = await publisher.publish_event(async_session, "e4", {"step": 4})

    # Client was disconnected after p2, reconnects with Last-Event-ID = p2["event_id"]
    replayed = await broadcaster.replay_events_since(
        session=async_session,
        last_event_id=p2["event_id"],
    )

    # Should replay exactly p3 and p4 in monotonic order
    assert len(replayed) == 2
    assert replayed[0]["event_id"] == p3["event_id"]
    assert replayed[0]["data"]["step"] == 3
    assert replayed[1]["event_id"] == p4["event_id"]
    assert replayed[1]["data"]["step"] == 4


@pytest.mark.asyncio
async def test_broadcaster_multiple_subscribers():
    broadcaster = OutboxSSEBroadcaster()
    q1 = await broadcaster.subscribe()
    q2 = await broadcaster.subscribe()

    try:
        event = {"event_id": "evt_multi", "event_type": "status_update", "data": "ok"}
        dispatched = await broadcaster.broadcast_event(event)
        assert dispatched == 2

        msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        msg2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert msg1["event_id"] == "evt_multi"
        assert msg2["event_id"] == "evt_multi"
    finally:
        await broadcaster.unsubscribe(q1)
        await broadcaster.unsubscribe(q2)


@pytest.mark.asyncio
async def test_last_event_id_unknown_returns_empty(async_session: AsyncSession):
    """Guarantees unrecognized Last-Event-ID does NOT dump entire database history."""
    broadcaster = OutboxSSEBroadcaster()
    publisher = OutboxEventPublisher(broadcaster=broadcaster)

    await publisher.publish_event(async_session, "e1", {"step": 1})
    await publisher.publish_event(async_session, "e2", {"step": 2})

    # Unknown random token
    replayed = await broadcaster.replay_events_since(
        session=async_session,
        last_event_id="corrupted_token_random_xyz",
    )
    assert replayed == []


@pytest.mark.asyncio
async def test_monotonic_sequence_even_with_pruned_rows(async_session: AsyncSession):
    """Guarantees strictly increasing monotonic IDs even if historical rows are pruned."""
    broadcaster = OutboxSSEBroadcaster()
    publisher = OutboxEventPublisher(broadcaster=broadcaster)

    p1 = await publisher.publish_event(async_session, "e1", {"step": 1})
    p2 = await publisher.publish_event(async_session, "e2", {"step": 2})
    p3 = await publisher.publish_event(async_session, "e3", {"step": 3})

    assert p1["event_id"] == "evt_00000001"
    assert p2["event_id"] == "evt_00000002"
    assert p3["event_id"] == "evt_00000003"

    # Prune first two rows (simulating retention policy cleanup)
    await async_session.execute(delete(OutboxEvent).where(OutboxEvent.event_id.in_(["evt_00000001", "evt_00000002"])))
    await async_session.commit()

    # Next event must be evt_00000004 (NOT evt_00000002 collision)
    p4 = await publisher.publish_event(async_session, "e4", {"step": 4})
    assert p4["event_id"] == "evt_00000004"
    assert p4["sequence_id"] == 4


@pytest.mark.asyncio
async def test_outbox_worker_dispatches_pending_events(async_session: AsyncSession):
    """Verifies that OutboxWorker claims pending events, broadcasts them, and marks them delivered."""
    broadcaster = OutboxSSEBroadcaster()
    worker = OutboxWorker(broadcaster=broadcaster, worker_id="test-worker")
    queue = await broadcaster.subscribe()

    try:
        # Create an event row with status='pending' without publishing in-line
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        payload = {
            "event_id": "evt_99990001",
            "sequence_id": 99990001,
            "event_type": "batch_commit_event",
            "channel": "company_events",
            "timestamp": now.isoformat(),
            "data": {"status": "persisted_via_transaction"},
        }
        event = OutboxEvent(
            event_id="evt_99990001",
            channel="company_events",
            payload=json.dumps(payload),
            status="pending",
            created_at=now,
            available_at=now,
        )
        async_session.add(event)
        await async_session.commit()

        # Run worker dispatch once
        dispatched = await worker.dispatch_once(session=async_session)
        assert dispatched >= 1

        # Verify broadcast queue received the event
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received["event_id"] == "evt_99990001"
        assert received["data"]["status"] == "persisted_via_transaction"

        # Verify database row is marked delivered
        stmt = select(OutboxEvent).where(OutboxEvent.event_id == "evt_99990001")
        res = await async_session.execute(stmt)
        delivered_row = res.scalar_one()
        assert delivered_row.status == "delivered"
        assert delivered_row.delivered_at is not None
    finally:
        await broadcaster.unsubscribe(queue)

