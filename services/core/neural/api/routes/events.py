from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from neural.api.deps import (
    get_container_manager,
    get_container_repo,
    get_db_session,
    get_employee_repo,
    get_meeting_repo,
    get_project_repo,
    get_task_repo,
)
from neural.api.routes.employees import serialize_employee_model
from neural.containers.manager import ContainerManager
from neural.db.models import OutboxEvent
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    MeetingRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.events.outbox import (
    OutboxEventPublisher,
    format_sse_frame,
    get_sse_broadcaster,
    utc_now,
)
from neural.observability.telemetry import SystemTelemetryCollector
from neural.workstations.layout import CANONICAL_WORKSTATIONS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["Events & Live Gateway"])


@router.get("/api/v1/events/stream")
@router.get("/events/stream")
async def stream_company_events(
    request: Request,
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    query_last_event_id: Optional[str] = Query(None, alias="last_event_id"),
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """
    Persistent SSE Gateway for real-time operational intelligence.
    Supports reconnection and monotonic replay using the Last-Event-ID header.
    Emits W3C EventSource frames with periodic keepalive comments.
    """
    effective_last_id = last_event_id or query_last_event_id
    broadcaster = get_sse_broadcaster()

    async def event_generator() -> AsyncGenerator[str, None]:
        queue = await broadcaster.subscribe()
        try:
            # 1. Monotonic replay if client reconnects with Last-Event-ID
            if effective_last_id:
                replayed_events = await broadcaster.replay_events_since(
                    session=session,
                    last_event_id=effective_last_id,
                )
                for event in replayed_events:
                    yield format_sse_frame(
                        event_id=event.get("event_id", ""),
                        event_type=event.get("event_type", "message"),
                        data=event,
                    )

            # 2. Live event loop with keepalive
            while True:
                # Check for client disconnect
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield format_sse_frame(
                        event_id=event.get("event_id", ""),
                        event_type=event.get("event_type", "message"),
                        data=event,
                    )
                except asyncio.TimeoutError:
                    # Send standard SSE keepalive comment to prevent socket timeout
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/v1/snapshot")
@router.get("/snapshot")
@router.get("/api/v1/events/snapshot")
async def get_system_snapshot(
    session: AsyncSession = Depends(get_db_session),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    project_repo: ProjectRepository = Depends(get_project_repo),
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_mgr: ContainerManager = Depends(get_container_manager),
) -> dict[str, Any]:
    """
    Periodic Snapshot Reconciliation endpoint.
    Provides authoritative ground truth for client bootstrapping or recovery after extended disconnects.
    """
    # 1. Latest outbox event ID
    stmt_last_evt = select(OutboxEvent).order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc()).limit(1)
    last_evt_res = await session.execute(stmt_last_evt)
    last_evt = last_evt_res.scalar_one_or_none()
    last_event_id = last_evt.event_id if last_evt else None

    # 2. Employees & Workstations
    employees = await employee_repo.list_all()
    serialized_employees: list[dict[str, Any]] = []
    occupied_map: dict[str, Any] = {}

    for emp in employees:
        desktop_spec = await container_mgr.get_desktop_by_employee_id(
            emp.employee_id,
            container_repo=container_repo,
            employee_repo=employee_repo,
        )
        desktop_url = desktop_spec.get("desktop_url") if desktop_spec else None
        serialized = serialize_employee_model(emp, desktop_url=desktop_url).model_dump()
        serialized_employees.append(serialized)
        if emp.desk_id:
            occupied_map[emp.desk_id] = emp

    workstations = []
    for ws in CANONICAL_WORKSTATIONS:
        desk_id = ws["id"]
        emp = occupied_map.get(desk_id)
        workstations.append(
            {
                "id": desk_id,
                "name": ws["name"],
                "department": ws["department"],
                "occupant_id": emp.employee_id if emp else None,
                "occupant_name": emp.name if emp else None,
            }
        )

    # 3. Active Projects & Tasks
    projects = await project_repo.list_all()
    serialized_projects = [
        {
            "id": p.id,
            "name": p.name,
            "status": p.status.value if hasattr(p.status, "value") else str(p.status),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in projects
    ]

    # 4. Active Meetings
    recent_meetings = await meeting_repo.list_recent(limit=5)
    active_meeting = None
    for m in recent_meetings:
        m_status = m.status.value if hasattr(m.status, "value") else str(m.status)
        if m_status == "in_progress":
            active_meeting = {
                "id": m.id,
                "title": m.title,
                "meeting_type": m.meeting_type.value if hasattr(m.meeting_type, "value") else str(m.meeting_type),
                "status": m_status,
                "active_speaker_id": getattr(m, "active_speaker_id", None),
                "participants": m.participants or [],
            }
            break

    # 5. Telemetry
    telemetry = SystemTelemetryCollector.collect_all_telemetry()

    return {
        "timestamp": utc_now().isoformat(),
        "last_event_id": last_event_id,
        "employee_count": len(serialized_employees),
        "employees": serialized_employees,
        "workstations": workstations,
        "active_projects": serialized_projects,
        "active_meeting": active_meeting,
        "telemetry": telemetry,
    }


@router.post("/api/v1/events/publish")
async def publish_company_event(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Test/Internal endpoint to publish an outbox event.
    """
    event_type = payload.get("event_type", "custom_event")
    data = payload.get("data", {})
    channel = payload.get("channel", "company_events")

    publisher = OutboxEventPublisher()
    try:
        created = await publisher.publish_event(
            session=session,
            event_type=event_type,
            data=data,
            channel=channel,
            auto_commit=True,
        )
        return created
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to publish event: {e}") from e
