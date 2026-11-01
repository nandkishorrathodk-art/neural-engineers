from typing import Optional

from fastapi import APIRouter, Depends, Query
from neural.api.deps import get_message_bus, get_message_repo
from neural.api.schemas.requests import (
    BroadcastRequest,
    DepartmentBroadcastRequest,
    DirectMessageRequest,
)
from neural.api.schemas.responses import MessageResponse
from neural.comms.bus import MessageBus
from neural.comms.messages import BroadcastMessage, DepartmentBroadcastMessage, DirectMessage
from neural.db.repositories import MessageRepository

router = APIRouter(prefix="/comms", tags=["Communications"])


@router.post("/broadcast")
async def send_executive_broadcast(
    request: BroadcastRequest,
    bus: MessageBus = Depends(get_message_bus),
    message_repo: MessageRepository = Depends(get_message_repo)
):
    msg = BroadcastMessage(
        title=request.title,
        content=request.content,
        sender=request.sender,
        priority=request.priority,
        channel="boss:broadcast"
    )
    saved = await message_repo.save_message(msg, channel="boss:broadcast")
    receivers = await bus.publish("boss:broadcast", msg)
    return {
        "status": "published",
        "channel": "boss:broadcast",
        "receivers": receivers,
        "message_id": saved.message_id
    }


@router.post("/department/{department}")
async def send_department_broadcast(
    department: str,
    request: DepartmentBroadcastRequest,
    bus: MessageBus = Depends(get_message_bus),
    message_repo: MessageRepository = Depends(get_message_repo)
):
    channel = f"dept:{department}:broadcast"
    msg = DepartmentBroadcastMessage(
        sender=request.sender,
        department=department,
        title=request.title,
        content=request.content,
        priority=request.priority,
        channel=channel
    )
    saved = await message_repo.save_message(msg, channel=channel)
    receivers = await bus.publish(channel, msg)
    return {
        "status": "published",
        "department": department,
        "channel": channel,
        "receivers": receivers,
        "message_id": saved.message_id
    }


@router.post("/direct")
async def send_direct_message(
    request: DirectMessageRequest,
    bus: MessageBus = Depends(get_message_bus),
    message_repo: MessageRepository = Depends(get_message_repo)
):
    channel = f"employee:{request.recipient}:inbox"
    msg = DirectMessage(
        sender=request.sender,
        recipient=request.recipient,
        content=request.content,
        subject=request.subject,
        channel=channel
    )
    saved = await message_repo.save_message(msg, channel=channel)
    receivers = await bus.publish(channel, msg)
    return {
        "status": "sent",
        "recipient": request.recipient,
        "channel": channel,
        "receivers": receivers,
        "message_id": saved.message_id
    }


@router.get("/messages", response_model=list[MessageResponse])
async def list_messages(
    channel: Optional[str] = Query(None, description="Filter by channel"),
    employee_id: Optional[str] = Query(None, description="Filter by employee"),
    department: Optional[str] = Query(None, description="Filter by department"),
    limit: int = Query(50, ge=1, le=200),
    message_repo: MessageRepository = Depends(get_message_repo)
):
    if channel:
        messages = await message_repo.list_by_channel(channel, limit=limit)
    elif employee_id:
        messages = await message_repo.list_by_employee(employee_id, limit=limit)
    elif department:
        messages = await message_repo.list_by_department(department, limit=limit)
    else:
        messages = await message_repo.list_recent(limit=limit)

    return [
        MessageResponse(
            id=m.id,
            message_id=m.message_id,
            message_type=m.message_type,
            channel=m.channel,
            sender=m.sender,
            recipient=m.recipient,
            department=m.department,
            correlation_id=m.correlation_id,
            payload=m.payload or {},
            created_at=m.created_at
        )
        for m in messages
    ]


@router.get("/channels")
async def list_channels(bus: MessageBus = Depends(get_message_bus)):
    return {
        "channels": bus.list_standard_channels(),
        "active_listeners": len(bus._active_listeners)
    }
