import asyncio

import pytest
from neural.comms.bus import MessageBus
from neural.comms.messages import (
    BaseMessage,
    DepartmentBroadcastMessage,
    DirectMessage,
)
from neural.db.repositories import MessageRepository
from sqlalchemy.ext.asyncio import AsyncSession

# =========================================================================
# 1. Pub/Sub Multi-Subscriber & Channel Routing Tests
# =========================================================================

@pytest.mark.asyncio
async def test_bus_multi_subscriber_broadcast():
    """Verify multiple subscribers on the same channel receive the broadcast."""
    bus = MessageBus(prefer_in_memory=True)
    received_sub1 = []
    received_sub2 = []

    async def cb1(msg: BaseMessage):
        received_sub1.append(msg)

    async def cb2(msg: BaseMessage):
        received_sub2.append(msg)

    sub1_id = await bus.subscribe("boss:broadcast", cb1)
    await bus.subscribe("boss:broadcast", cb2)

    await bus.broadcast(title="All-Hands", content="Quarterly company sync at 10:00")
    await asyncio.sleep(0.05)

    assert len(received_sub1) == 1
    assert len(received_sub2) == 1
    assert received_sub1[0].payload["title"] == "All-Hands"
    assert received_sub2[0].payload["title"] == "All-Hands"

    # Unsubscribe sub1, publish second message
    await bus.unsubscribe("boss:broadcast", sub1_id)
    await bus.broadcast(title="Follow-Up", content="Meeting concluded")
    await asyncio.sleep(0.05)

    assert len(received_sub1) == 1  # No new message
    assert len(received_sub2) == 2  # Received second message

    await bus.close()


@pytest.mark.asyncio
async def test_bus_direct_messaging():
    """Verify 1-to-1 direct messaging routes to employee:{id}:inbox."""
    bus = MessageBus(prefer_in_memory=True)
    inbox = []

    async def on_inbox(msg: BaseMessage):
        inbox.append(msg)

    await bus.subscribe("employee:EMP-ALEX:inbox", on_inbox)

    await bus.send_direct_message(
        sender="EMP-MAYA",
        recipient="EMP-ALEX",
        subject="PR Review",
        content="Could you check the changes in auth_service.py?"
    )
    await asyncio.sleep(0.05)

    assert len(inbox) == 1
    msg = inbox[0]
    assert msg.sender == "EMP-MAYA"
    assert msg.recipient == "EMP-ALEX"
    assert msg.payload["subject"] == "PR Review"
    assert msg.payload["content"] == "Could you check the changes in auth_service.py?"

    await bus.close()


@pytest.mark.asyncio
async def test_bus_department_broadcast_channels():
    """Verify department broadcast routes to dept:{dept}:broadcast channel."""
    bus = MessageBus(prefer_in_memory=True)
    eng_inbox = []
    design_inbox = []

    async def on_eng(msg: BaseMessage):
        eng_inbox.append(msg)

    async def on_design(msg: BaseMessage):
        design_inbox.append(msg)

    await bus.subscribe_department("engineering", on_eng)
    await bus.subscribe_department("design", on_design)

    # Broadcast to engineering
    await bus.broadcast_to_department(
        department="engineering",
        title="Deploy Notice",
        content="Staging deployment starting in 5 minutes"
    )
    await asyncio.sleep(0.05)

    assert len(eng_inbox) == 1
    assert len(design_inbox) == 0
    assert eng_inbox[0].payload["title"] == "Deploy Notice"

    # Broadcast to design
    await bus.broadcast_to_department(
        department="design",
        title="Figma Update",
        content="New design tokens landed"
    )
    await asyncio.sleep(0.05)

    assert len(eng_inbox) == 1
    assert len(design_inbox) == 1
    assert design_inbox[0].payload["title"] == "Figma Update"

    await bus.close()


# =========================================================================
# 2. Typed Message Serialization Tests
# =========================================================================

def test_typed_message_deserialization():
    """Verify BaseMessage.from_json correctly maps typed subclasses."""
    # DirectMessage
    dm = DirectMessage(
        sender="Alex",
        recipient="Jordan",
        content="Let's pair on the API",
        subject="Pair Programming"
    )
    dm_json = dm.to_json()
    parsed_dm = BaseMessage.from_json(dm_json)
    assert isinstance(parsed_dm, DirectMessage)
    assert parsed_dm.recipient == "Jordan"
    assert parsed_dm.content == "Let's pair on the API"

    # DepartmentBroadcastMessage
    dept_msg = DepartmentBroadcastMessage(
        sender="Lead",
        department="DevOps",
        title="Cluster Upgrade",
        content="Kubernetes 1.30 scheduled"
    )
    dept_json = dept_msg.to_json()
    parsed_dept = BaseMessage.from_json(dept_json)
    assert isinstance(parsed_dept, DepartmentBroadcastMessage)
    assert parsed_dept.department == "DevOps"


# =========================================================================
# 3. Database Persistence & Audit Trail Tests
# =========================================================================

@pytest.mark.asyncio
async def test_message_repository_persistence_and_queries(async_session: AsyncSession):
    """Verify MessageRepository saves and filters messages in `messages` table."""
    repo = MessageRepository(async_session)

    # 1. Save broadcast
    msg1 = await repo.save_message(
        {
            "message_id": "MSG-BCAST-01",
            "message_type": "broadcast",
            "channel": "boss:broadcast",
            "sender": "boss",
            "payload": {"title": "Company Kickoff"}
        },
        channel="boss:broadcast"
    )
    assert msg1.id is not None
    assert msg1.message_id == "MSG-BCAST-01"

    # 2. Save direct message
    msg2 = await repo.save_message(
        DirectMessage(
            message_id="MSG-DM-01",
            sender="EMP-01",
            recipient="EMP-02",
            content="Hello EMP-02",
            channel="employee:EMP-02:inbox"
        ),
        channel="employee:EMP-02:inbox"
    )
    assert msg2.recipient == "EMP-02"

    # 3. Save department broadcast
    msg3 = await repo.save_message(
        DepartmentBroadcastMessage(
            message_id="MSG-DEPT-01",
            sender="boss",
            department="QA",
            title="Smoke test run",
            content="Please run smoke tests",
            channel="dept:QA:broadcast"
        ),
        channel="dept:QA:broadcast"
    )
    assert msg3.department == "QA"

    # 4. Queries
    # By message ID
    fetched = await repo.get_by_message_id("MSG-BCAST-01")
    assert fetched is not None
    assert fetched.payload["title"] == "Company Kickoff"

    # By channel
    chan_msgs = await repo.list_by_channel("boss:broadcast")
    assert len(chan_msgs) == 1
    assert chan_msgs[0].message_id == "MSG-BCAST-01"

    # By employee
    emp_msgs = await repo.list_by_employee("EMP-02")
    assert len(emp_msgs) == 1
    assert emp_msgs[0].message_id == "MSG-DM-01"

    # By department
    dept_msgs = await repo.list_by_department("QA")
    assert len(dept_msgs) == 1
    assert dept_msgs[0].message_id == "MSG-DEPT-01"

    # Recent messages
    recent = await repo.list_recent(limit=10)
    assert len(recent) == 3


@pytest.mark.asyncio
async def test_bus_publish_auto_persists_to_db(async_session: AsyncSession):
    """Verify that MessageBus.publish auto-persists to the database when db_session is set."""
    bus = MessageBus(prefer_in_memory=True, db_session=async_session)

    await bus.send_direct_message(
        sender="EMP-SENDER",
        recipient="EMP-RECEIVER",
        content="Audited direct communication"
    )

    repo = MessageRepository(async_session)
    msgs = await repo.list_by_employee("EMP-RECEIVER")
    assert len(msgs) == 1
    assert msgs[0].payload["content"] == "Audited direct communication"

    await bus.close()


@pytest.mark.asyncio
async def test_message_repository_idempotent_save(async_session: AsyncSession):
    """Verify that saving a message with an identical message_id is idempotent and safe."""
    repo = MessageRepository(async_session)
    msg = DirectMessage(
        message_id="MSG-DUP-01",
        sender="EMP-A",
        recipient="EMP-B",
        content="First save"
    )

    saved1 = await repo.save_message(msg)
    assert saved1.message_id == "MSG-DUP-01"

    # Saving again with same message_id should return existing row without IntegrityError
    saved2 = await repo.save_message(msg)
    assert saved2.message_id == "MSG-DUP-01"
    assert saved2.id == saved1.id


@pytest.mark.asyncio
async def test_bus_broadcast_and_task_event_audit(async_session: AsyncSession):
    """Verify bus.broadcast and publish_task_event correctly accept db_session and persist."""
    bus = MessageBus(prefer_in_memory=True)
    repo = MessageRepository(async_session)

    # Broadcast with db_session
    await bus.broadcast(
        title="Audited Broadcast",
        content="Important company alert",
        db_session=async_session
    )
    bcast_msgs = await repo.list_by_channel("boss:broadcast")
    assert any(m.payload.get("title") == "Audited Broadcast" for m in bcast_msgs)

    # Task event with db_session
    await bus.publish_task_event(
        task_id="TASK-AUDIT-01",
        event_type="task_completed",
        status="completed",
        details={"audit": True},
        db_session=async_session
    )
    task_msgs = await repo.list_by_channel("tasks:TASK-AUDIT-01:events")
    assert len(task_msgs) == 1
    assert task_msgs[0].payload.get("event_type") == "task_completed"

    await bus.close()
