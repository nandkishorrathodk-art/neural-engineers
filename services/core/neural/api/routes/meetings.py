
from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_action_item_repo,
    get_approval_repo,
    get_audit_repo,
    get_employee_repo,
    get_meeting_repo,
    get_perf_repo,
    get_project_repo,
    get_task_repo,
)
from neural.api.schemas.requests import (
    ConductMeetingRequest,
    CreateActionItemRequest,
    RecordDecisionRequest,
    ScheduleMeetingRequest,
    SubmitProgressReportRequest,
    TriggerBlockerMeetingRequest,
    TriggerPhaseMeetingRequest,
    UpdateActionItemRequest,
)
from neural.api.schemas.responses import (
    ActionItemResponse,
    ExecutiveBriefingResponse,
    MeetingDetailResponse,
    MeetingResponse,
)
from neural.constants import ActionItemStatus
from neural.db.repositories import (
    ActionItemRepository,
    ApprovalRepository,
    AuditRepository,
    EmployeeRepository,
    MeetingRepository,
    PerformanceRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.exceptions import FabricatedProgressError
from neural.meetings.actions import ActionItemTracker
from neural.meetings.briefings import ExecutiveBriefingGenerator
from neural.meetings.conductor import MeetingConductor
from neural.meetings.participants import ParticipantSelector
from neural.meetings.scheduler import MeetingScheduler

router = APIRouter(prefix="/meetings", tags=["Boardroom & Meetings"])


def serialize_action_item_model(ai) -> ActionItemResponse:
    return ActionItemResponse(
        id=ai.id,
        meeting_id=ai.meeting_id,
        description=ai.description,
        assigned_to=ai.assigned_to,
        deadline=ai.deadline,
        status=ai.status,
        created_at=ai.created_at,
        is_overdue=ActionItemTracker.is_overdue(ai)
    )


@router.get("/", response_model=list[MeetingResponse])
async def list_meetings(
    limit: int = Query(20, ge=1, le=100),
    meeting_repo: MeetingRepository = Depends(get_meeting_repo)
):
    meetings = await meeting_repo.list_recent(limit=limit)
    return [
        MeetingResponse(
            id=m.id,
            meeting_id=m.meeting_id,
            title=m.title,
            meeting_type=m.meeting_type,
            status=m.status,
            scheduled_at=m.scheduled_at,
            participants=m.participants,
            agenda=m.agenda,
            minutes=m.minutes
        )
        for m in meetings
    ]


@router.post("/schedule", response_model=MeetingResponse)
async def schedule_meeting(
    payload: ScheduleMeetingRequest,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    audit_repo: AuditRepository = Depends(get_audit_repo)
):
    conductor = MeetingConductor(meeting_repo)
    meeting = await conductor.schedule_meeting(
        title=payload.title,
        meeting_type=payload.meeting_type,
        agenda=payload.agenda,
        participants=payload.participants
    )

    await audit_repo.log(
        actor="BossAI",
        action="SCHEDULE_MEETING",
        resource_type="Meeting",
        resource_id=meeting.meeting_id,
        details={"title": meeting.title, "type": meeting.meeting_type.value}
    )

    return MeetingResponse(
        id=meeting.id,
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        status=meeting.status,
        scheduled_at=meeting.scheduled_at,
        participants=meeting.participants,
        agenda=meeting.agenda,
        minutes=meeting.minutes
    )


@router.get("/briefing", response_model=ExecutiveBriefingResponse)
async def get_executive_briefing(
    project_id: str | None = None,
    project_repo: ProjectRepository = Depends(get_project_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    perf_repo: PerformanceRepository = Depends(get_perf_repo),
    approval_repo: ApprovalRepository = Depends(get_approval_repo),
    action_item_repo: ActionItemRepository = Depends(get_action_item_repo)
):
    generator = ExecutiveBriefingGenerator(
        project_repo=project_repo,
        task_repo=task_repo,
        employee_repo=employee_repo,
        meeting_repo=meeting_repo,
        perf_repo=perf_repo,
        approval_repo=approval_repo,
        action_item_repo=action_item_repo
    )
    briefing = await generator.generate_briefing(project_id=project_id)
    return ExecutiveBriefingResponse(**briefing)


@router.get("/action-items", response_model=list[ActionItemResponse])
async def list_action_items(
    status: ActionItemStatus | None = None,
    overdue_only: bool = False,
    action_item_repo: ActionItemRepository = Depends(get_action_item_repo)
):
    tracker = ActionItemTracker(action_item_repo)
    if overdue_only:
        items = await tracker.list_overdue()
    elif status:
        all_items = await tracker.repo.list_all()
        items = [i for i in all_items if i.status == status]
    else:
        items = await tracker.repo.list_all()

    return [serialize_action_item_model(i) for i in items]


@router.post("/action-items", response_model=ActionItemResponse)
async def create_action_item(
    payload: CreateActionItemRequest,
    action_item_repo: ActionItemRepository = Depends(get_action_item_repo),
    meeting_repo: MeetingRepository = Depends(get_meeting_repo)
):
    meeting = await meeting_repo.get_by_meeting_id(payload.meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting '{payload.meeting_id}' not found")

    tracker = ActionItemTracker(action_item_repo)
    item = await tracker.create_action_item(
        meeting_id=meeting.id,
        description=payload.description,
        assigned_to=payload.assigned_to,
        deadline=payload.deadline
    )
    return serialize_action_item_model(item)


@router.patch("/action-items/{action_item_id}", response_model=ActionItemResponse)
async def update_action_item(
    action_item_id: str,
    payload: UpdateActionItemRequest,
    action_item_repo: ActionItemRepository = Depends(get_action_item_repo)
):
    tracker = ActionItemTracker(action_item_repo)
    item = await tracker.repo.get_by_id(action_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Action item not found")

    kwargs = {}
    if payload.status is not None:
        kwargs["status"] = payload.status
    if payload.deadline is not None:
        kwargs["deadline"] = payload.deadline

    updated = await tracker.repo.update_item(action_item_id, **kwargs)
    return serialize_action_item_model(updated)


@router.post("/trigger/standup", response_model=MeetingResponse)
async def trigger_standup(
    project_id: str | None = None,
    department: str | None = None,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    task_repo: TaskRepository = Depends(get_task_repo)
):
    conductor = MeetingConductor(meeting_repo)
    selector = ParticipantSelector(employee_repo, task_repo)
    scheduler = MeetingScheduler(
        meeting_repo=meeting_repo,
        conductor=conductor,
        participant_selector=selector,
        task_repo=task_repo
    )
    meeting = await scheduler.trigger_daily_standup(project_id=project_id, department=department)
    return MeetingResponse(
        id=meeting.id,
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        status=meeting.status,
        scheduled_at=meeting.scheduled_at,
        participants=meeting.participants,
        agenda=meeting.agenda,
        minutes=meeting.minutes
    )


@router.post("/trigger/blocker", response_model=MeetingResponse)
async def trigger_blocker_meeting(
    payload: TriggerBlockerMeetingRequest,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    task_repo: TaskRepository = Depends(get_task_repo)
):
    conductor = MeetingConductor(meeting_repo)
    selector = ParticipantSelector(employee_repo, task_repo)
    scheduler = MeetingScheduler(
        meeting_repo=meeting_repo,
        conductor=conductor,
        participant_selector=selector,
        task_repo=task_repo
    )
    meeting = await scheduler.on_task_blocked(
        task_id=payload.task_id,
        blocker_reason=payload.blocker_reason,
        project_id=payload.project_id
    )
    return MeetingResponse(
        id=meeting.id,
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        status=meeting.status,
        scheduled_at=meeting.scheduled_at,
        participants=meeting.participants,
        agenda=meeting.agenda,
        minutes=meeting.minutes
    )


@router.post("/trigger/phase", response_model=MeetingResponse)
async def trigger_phase_meeting(
    payload: TriggerPhaseMeetingRequest,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    project_repo: ProjectRepository = Depends(get_project_repo)
):
    conductor = MeetingConductor(meeting_repo)
    selector = ParticipantSelector(employee_repo, task_repo)
    scheduler = MeetingScheduler(
        meeting_repo=meeting_repo,
        conductor=conductor,
        participant_selector=selector,
        task_repo=task_repo,
        project_repo=project_repo
    )
    meeting = await scheduler.on_phase_completed(
        project_id=payload.project_id,
        phase_id=payload.phase_id,
        completed_tasks=payload.completed_tasks
    )
    return MeetingResponse(
        id=meeting.id,
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        status=meeting.status,
        scheduled_at=meeting.scheduled_at,
        participants=meeting.participants,
        agenda=meeting.agenda,
        minutes=meeting.minutes
    )


@router.get("/{meeting_id}", response_model=MeetingDetailResponse)
async def get_meeting_details(
    meeting_id: str,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    action_item_repo: ActionItemRepository = Depends(get_action_item_repo)
):
    meeting = await meeting_repo.get_by_meeting_id(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    action_items = await action_item_repo.list_by_meeting(meeting.id)
    return MeetingDetailResponse(
        id=meeting.id,
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        status=meeting.status,
        scheduled_at=meeting.scheduled_at,
        started_at=meeting.started_at,
        concluded_at=meeting.concluded_at,
        participants=meeting.participants,
        agenda=meeting.agenda,
        minutes=meeting.minutes,
        decisions=meeting.decisions or [],
        action_items=[serialize_action_item_model(ai) for ai in action_items]
    )


@router.post("/{meeting_id}/start", response_model=MeetingResponse)
async def start_meeting(
    meeting_id: str,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo)
):
    conductor = MeetingConductor(meeting_repo)
    try:
        meeting = await conductor.convene_meeting(meeting_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return MeetingResponse(
        id=meeting.id,
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        status=meeting.status,
        scheduled_at=meeting.scheduled_at,
        participants=meeting.participants,
        agenda=meeting.agenda,
        minutes=meeting.minutes
    )


@router.post("/{meeting_id}/reports")
async def submit_progress_report(
    meeting_id: str,
    payload: SubmitProgressReportRequest,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo)
):
    conductor = MeetingConductor(meeting_repo)
    try:
        report = await conductor.collect_report(
            meeting_id=meeting_id,
            report_data=payload.model_dump()
        )
        return {"status": "accepted", "report": report}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FabricatedProgressError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{meeting_id}/decisions")
async def record_decision(
    meeting_id: str,
    payload: RecordDecisionRequest,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo)
):
    conductor = MeetingConductor(meeting_repo)
    try:
        dec = await conductor.record_decision(
            meeting_id=meeting_id,
            topic=payload.topic,
            decision=payload.decision,
            rationale=payload.rationale,
            decided_by=payload.decided_by,
            impact=payload.impact
        )
        return {"status": "recorded", "decision": dec}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{meeting_id}/conclude", response_model=MeetingDetailResponse)
async def conclude_meeting(
    meeting_id: str,
    summary: str | None = None,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    action_item_repo: ActionItemRepository = Depends(get_action_item_repo)
):
    conductor = MeetingConductor(meeting_repo, action_item_repo=action_item_repo)
    try:
        meeting = await conductor.conclude_meeting(meeting_id, summary=summary)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    action_items = await action_item_repo.list_by_meeting(meeting.id)
    return MeetingDetailResponse(
        id=meeting.id,
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        status=meeting.status,
        scheduled_at=meeting.scheduled_at,
        started_at=meeting.started_at,
        concluded_at=meeting.concluded_at,
        participants=meeting.participants,
        agenda=meeting.agenda,
        minutes=meeting.minutes,
        decisions=meeting.decisions or [],
        action_items=[serialize_action_item_model(ai) for ai in action_items]
    )


@router.post("/{meeting_id}/conduct", response_model=MeetingDetailResponse)
async def conduct_meeting(
    meeting_id: str,
    payload: ConductMeetingRequest,
    meeting_repo: MeetingRepository = Depends(get_meeting_repo),
    action_item_repo: ActionItemRepository = Depends(get_action_item_repo)
):
    conductor = MeetingConductor(meeting_repo, action_item_repo=action_item_repo)
    try:
        meeting = await conductor.conduct_standup(
            meeting_id=meeting_id,
            employee_reports=payload.employee_reports,
            decisions=payload.decisions,
            action_items=payload.action_items,
            carry_over=payload.carry_over
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FabricatedProgressError as e:
        raise HTTPException(status_code=400, detail=str(e))

    action_items = await action_item_repo.list_by_meeting(meeting.id)
    return MeetingDetailResponse(
        id=meeting.id,
        meeting_id=meeting.meeting_id,
        title=meeting.title,
        meeting_type=meeting.meeting_type,
        status=meeting.status,
        scheduled_at=meeting.scheduled_at,
        started_at=meeting.started_at,
        concluded_at=meeting.concluded_at,
        participants=meeting.participants,
        agenda=meeting.agenda,
        minutes=meeting.minutes,
        decisions=meeting.decisions or [],
        action_items=[serialize_action_item_model(ai) for ai in action_items]
    )
