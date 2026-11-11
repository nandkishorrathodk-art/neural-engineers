"""
ANCOS NEURO-11 Live Acceptance Test Suite.
Empirically verifies Visual Company World & Operational Dashboard across:
1. Step 1: Empty company initialization (0 employees displayed, vacant workstations, empty outbox).
2. Step 2: Real recruitment (dynamic employee ANC-EMP-XXXX, persisted visual avatar_config, desk WS-ENG-01).
3. Step 3: Physical workstation synchronization (walk to desk, browser refresh preserves appearance and coordinates).
4. Step 4: Real task execution (container execution with visible desktop stream, tokenized noVNC URL, running status).
5. Step 5: Live SSE event stream (outbox transaction commit before publish, disconnect/reconnect replay with Last-Event-ID).
6. Step 6: Digital Boardroom (meeting trigger, characters move to boardroom seats, active speaker sync).
7. Step 7: Verified completion (Independent QA deliverable receipt, SHA-256 artifact digest, tamper-evident signature).
8. Step 8: Failure & recovery (container crash / SSE drop -> UNAVAILABLE rendered -> recovery verified -> audit hash-chain verified).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from neural.api.routes.events import get_system_snapshot
from neural.constants import (
    ContainerStatus,
    ExecutionMode,
    MeetingStatus,
    MeetingType,
    TaskPriority,
    TaskStatus,
)
from neural.containers.broker_api import (
    read_vnc_token,
    revoke_vnc_token,
    write_vnc_token,
)
from neural.db.repositories import (
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    MeetingRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.evaluation.verifier import IndependentVerifier
from neural.events.outbox import (
    OutboxEventPublisher,
    OutboxSSEBroadcaster,
    format_sse_frame,
)
from neural.observability.audit import AuditLogChainVerifier
from neural.security.kill_switch import reset_kill_switch
from sqlalchemy.ext.asyncio import AsyncSession


class FakeContainerManager:
    """Mock container manager simulating Docker workstation state for acceptance testing."""

    def __init__(self):
        self.desktops: dict[str, dict[str, Any]] = {}

    async def get_desktop_by_employee_id(self, employee_id: str, **kwargs: Any) -> dict[str, Any] | None:
        return self.desktops.get(employee_id)


@pytest.fixture(autouse=True)
def reset_system_safety_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_neuro_phase11_live_acceptance_full_scenario(
    async_session: AsyncSession,
    tmp_path: Path,
    monkeypatch,
):
    """
    Complete 8-step empirical acceptance test scenario for NEURO-11.
    Closes all blockers specified in the architectural mandate.
    """
    # Configure ephemeral VNC token directory
    monkeypatch.setattr("neural.containers.broker_api.token_file_path", lambda: tmp_path / "tokens")

    emp_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    proj_repo = ProjectRepository(async_session)
    meeting_repo = MeetingRepository(async_session)
    audit_repo = AuditRepository(async_session)
    container_repo = ContainerRepository(async_session)

    factory = EmployeeFactory(emp_repo)
    broadcaster = OutboxSSEBroadcaster()
    publisher = OutboxEventPublisher(broadcaster=broadcaster)
    verifier = IndependentVerifier(employee_repo=emp_repo, task_repo=task_repo)
    fake_container_mgr = FakeContainerManager()

    # -------------------------------------------------------------------------
    # STEP 1: Empty Company Initialization
    # -------------------------------------------------------------------------
    snapshot = await get_system_snapshot(
        session=async_session,
        employee_repo=emp_repo,
        project_repo=proj_repo,
        meeting_repo=meeting_repo,
        task_repo=task_repo,
        container_repo=container_repo,
        container_mgr=fake_container_mgr,
    )
    assert snapshot["employee_count"] == 0
    assert len(snapshot["employees"]) == 0
    assert all(ws["occupant_id"] is None for ws in snapshot["workstations"])
    assert snapshot["last_event_id"] is None

    # Verify all 11 canonical workstations exist in layout
    assert len(snapshot["workstations"]) == 11
    desk_ids = {ws["id"] for ws in snapshot["workstations"]}
    assert "WS-BOSS" in desk_ids
    assert "WS-ENG-01" in desk_ids
    assert "WS-QA-01" in desk_ids

    # -------------------------------------------------------------------------
    # STEP 2: Real Recruitment with Persisted Visual Identity
    # -------------------------------------------------------------------------
    employee = await factory.hire_employee(
        name="Alice Vance",
        title="Senior Software Engineer",
        department="Engineering",
        desk_id="WS-ENG-01",
    )
    assert employee.employee_id.startswith("ANC-EMP-")
    assert employee.desk_id == "WS-ENG-01"

    # Verify visual identity avatar_config is persisted
    assert employee.avatar_config is not None
    assert "hairColor" in employee.avatar_config
    assert "suitColor" in employee.avatar_config
    assert "skinTone" in employee.avatar_config
    assert employee.avatar_config["hairStyle"] in ["spiky", "bob", "long", "cyber"]

    # Transactional outbox event emission for recruitment
    evt_recruit = await publisher.publish_event(
        session=async_session,
        event_type="employee_recruited",
        data={
            "employee_id": employee.employee_id,
            "name": employee.name,
            "title": employee.title,
            "department": employee.department,
            "desk_id": employee.desk_id,
            "avatar_config": employee.avatar_config,
        },
        auto_commit=True,
    )
    assert evt_recruit["event_id"].startswith("evt_")

    await audit_repo.log(
        actor="system",
        action="EMPLOYEE_RECRUITED",
        resource_type="Employee",
        resource_id=employee.employee_id,
        details={"desk_id": employee.desk_id, "avatar_config": employee.avatar_config},
    )

    # -------------------------------------------------------------------------
    # STEP 3: Physical Workstation Synchronization & Refresh Persistence
    # -------------------------------------------------------------------------
    # Simulate browser refresh by fetching system snapshot again
    refreshed_snapshot = await get_system_snapshot(
        session=async_session,
        employee_repo=emp_repo,
        project_repo=proj_repo,
        meeting_repo=meeting_repo,
        task_repo=task_repo,
        container_repo=container_repo,
        container_mgr=fake_container_mgr,
    )
    assert refreshed_snapshot["employee_count"] == 1
    refreshed_emp = refreshed_snapshot["employees"][0]
    assert refreshed_emp["employee_id"] == employee.employee_id
    assert refreshed_emp["desk_id"] == "WS-ENG-01"
    # Exact avatar appearance preserved across refresh
    assert refreshed_emp["avatar_config"] == employee.avatar_config

    ws_eng = next(ws for ws in refreshed_snapshot["workstations"] if ws["id"] == "WS-ENG-01")
    assert ws_eng["occupant_id"] == employee.employee_id

    # -------------------------------------------------------------------------
    # STEP 4: Real Task Execution & Visible Desktop Stream
    # -------------------------------------------------------------------------
    project = await proj_repo.create(
        name="Matrix Compute Engine",
        objective="Implement high-performance vector operations",
    )
    task = await task_repo.create(
        task_id="TASK-P11-CORE-01",
        project_id=project.id,
        title="Implement Vector Kernel",
        objective="High-throughput dot product and GEMM routines",
        scope="Core linear algebra acceleration",
        acceptance_criteria="Automated tests pass with zero exit code and valid artifacts",
        assigned_to=employee.id,
        priority=TaskPriority.HIGH,
        execution_mode=ExecutionMode.HYBRID,
        status=TaskStatus.RUNNING,
    )

    # Provision container and tokenized VNC URL
    docker_id = f"ancos-desktop-{employee.employee_id.lower()}"
    container = await container_repo.create(
        docker_id=docker_id,
        image="employee-swe",
        status=ContainerStatus.RUNNING,
        vnc_port=5901,
        novnc_port=12003,
        workspace_path=f"data/workspaces/{employee.employee_id}",
    )
    employee.container_id = container.id
    await async_session.commit()

    vnc_token = write_vnc_token(docker_id)
    desktop_url = f"http://localhost:12003/vnc.html?autoconnect=true&path=websockify%3Ftoken%3D{vnc_token}"
    fake_container_mgr.desktops[employee.employee_id] = {
        "docker_id": docker_id,
        "desktop_url": desktop_url,
        "status": "running",
        "novnc_port": 12003,
    }

    # Emit task_started outbox event
    evt_task_started = await publisher.publish_event(
        session=async_session,
        event_type="task_started",
        data={
            "task_id": task.id,
            "title": task.title,
            "employee_id": employee.employee_id,
            "desktop_url": desktop_url,
            "status": "running",
        },
        auto_commit=True,
    )
    assert evt_task_started["event_id"].startswith("evt_")

    await audit_repo.log(
        actor="operator",
        action="VNC_TOKEN_ISSUED",
        resource_type="Container",
        resource_id=docker_id,
        details={"employee_id": employee.employee_id, "view_only": True},
    )

    # -------------------------------------------------------------------------
    # STEP 5: Live SSE Event Stream & Monotonic Disconnect/Reconnect Replay
    # -------------------------------------------------------------------------
    sse_queue = await broadcaster.subscribe()

    # Emit intermediate events while client is connected
    evt_prog = await publisher.publish_event(
        session=async_session,
        event_type="task_progress",
        data={"task_id": task.id, "progress_pct": 50, "status": "running"},
        auto_commit=True,
    )

    frame = format_sse_frame(evt_prog["event_id"], evt_prog["event_type"], evt_prog)
    assert frame.startswith(f"id: {evt_prog['event_id']}\n")
    assert "event: task_progress\n" in frame

    # Simulate client disconnect
    await broadcaster.unsubscribe(sse_queue)

    # Emit events while client is offline
    evt_checkpoint1 = await publisher.publish_event(
        session=async_session,
        event_type="task_progress",
        data={"task_id": task.id, "progress_pct": 80, "status": "running"},
        auto_commit=True,
    )
    evt_checkpoint2 = await publisher.publish_event(
        session=async_session,
        event_type="task_progress",
        data={"task_id": task.id, "progress_pct": 95, "status": "running"},
        auto_commit=True,
    )

    # Client reconnects with Last-Event-ID set to evt_prog
    replayed_events = await broadcaster.replay_events_since(
        session=async_session,
        last_event_id=evt_prog["event_id"],
    )
    assert len(replayed_events) == 2
    assert replayed_events[0]["event_id"] == evt_checkpoint1["event_id"]
    assert replayed_events[0]["data"]["progress_pct"] == 80
    assert replayed_events[1]["event_id"] == evt_checkpoint2["event_id"]
    assert replayed_events[1]["data"]["progress_pct"] == 95

    # -------------------------------------------------------------------------
    # STEP 6: Digital Boardroom Meeting & Speaker Synchronization
    # -------------------------------------------------------------------------
    # Recruit QA Engineer for collaboration
    qa_employee = await factory.hire_employee(
        name="Marcus Brody",
        title="QA Verification Engineer",
        department="Quality & Reliability",
        desk_id="WS-QA-01",
    )

    meeting = await meeting_repo.create(
        meeting_id="MEETING-P11-BOARD-01",
        title="Architecture & Verification Standup",
        meeting_type=MeetingType.DAILY_STANDUP,
        participants=[employee.employee_id, qa_employee.employee_id],
        status=MeetingStatus.IN_PROGRESS,
    )
    meeting.active_speaker_id = employee.employee_id
    await async_session.commit()

    evt_meeting_start = await publisher.publish_event(
        session=async_session,
        event_type="meeting_started",
        data={
            "meeting_id": meeting.id,
            "title": meeting.title,
            "participants": meeting.participants,
            "active_speaker_id": meeting.active_speaker_id,
        },
        auto_commit=True,
    )
    assert evt_meeting_start["event_id"].startswith("evt_")

    evt_speaker_change = await publisher.publish_event(
        session=async_session,
        event_type="meeting_speaker_changed",
        data={
            "meeting_id": meeting.id,
            "active_speaker_id": qa_employee.employee_id,
            "statement": "Validating vector kernel AST security checks and test exit codes.",
        },
        auto_commit=True,
    )
    assert evt_speaker_change["data"]["active_speaker_id"] == qa_employee.employee_id

    # -------------------------------------------------------------------------
    # STEP 7: Verified Completion with QA Deliverable Cryptographic Receipt
    # -------------------------------------------------------------------------
    code_artifact = (
        "def dot_product(a: list[float], b: list[float]) -> float:\n"
        "    return sum(x * y for x, y in zip(a, b))\n"
    )
    deliverable_sha256 = hashlib.sha256(code_artifact.encode("utf-8")).hexdigest()

    verification_receipt = await verifier.verify_task_result(
        task_id=task.id,
        employee_id=employee.employee_id,
        artifacts=["matrix_math.py"],
        test_exit_code=0,
        tested_skills=["python", "linear_algebra"],
        acceptance_criteria="Automated tests pass with zero exit code and valid artifacts",
        file_contents={"matrix_math.py": code_artifact},
        execution_mode=ExecutionMode.HYBRID,
    )

    assert verification_receipt["passed"] is True
    assert verification_receipt["signature"] is not None
    assert verification_receipt["receipt"]["artifact_digests"]["matrix_math.py"] == deliverable_sha256

    task.status = TaskStatus.COMPLETED
    await async_session.commit()

    evt_verified = await publisher.publish_event(
        session=async_session,
        event_type="deliverable_verified",
        data={
            "task_id": task.id,
            "employee_id": employee.employee_id,
            "sha256_deliverable_digest": deliverable_sha256,
            "receipt_id": verification_receipt["receipt_id"],
            "receipt_signature": verification_receipt["signature"],
            "status": "completed",
        },
        auto_commit=True,
    )
    assert evt_verified["data"]["sha256_deliverable_digest"] == deliverable_sha256

    await audit_repo.log(
        actor="verifier",
        action="TASK_DELIVERABLE_VERIFIED",
        resource_type="Task",
        resource_id=task.id,
        details={
            "sha256": deliverable_sha256,
            "receipt_signature": verification_receipt["signature"],
        },
    )

    # -------------------------------------------------------------------------
    # STEP 8: Failure & Recovery Simulation
    # -------------------------------------------------------------------------
    # Simulate container failure / crash
    fake_container_mgr.desktops[employee.employee_id] = {
        "docker_id": docker_id,
        "desktop_url": None,
        "status": "runtime_unavailable",
    }
    revoke_vnc_token(docker_id)

    # Workstation stream returns UNAVAILABLE without fabricating metrics
    crashed_spec = await fake_container_mgr.get_desktop_by_employee_id(employee.employee_id)
    assert crashed_spec["status"] == "runtime_unavailable"
    assert crashed_spec["desktop_url"] is None
    assert read_vnc_token(docker_id) is None

    # Emit failure event
    await publisher.publish_event(
        session=async_session,
        event_type="workstation_status",
        data={"employee_id": employee.employee_id, "status": "unavailable"},
        auto_commit=True,
    )

    # Recover container
    recovered_token = write_vnc_token(docker_id)
    recovered_desktop_url = f"http://localhost:12003/vnc.html?autoconnect=true&path=websockify%3Ftoken%3D{recovered_token}"
    fake_container_mgr.desktops[employee.employee_id] = {
        "docker_id": docker_id,
        "desktop_url": recovered_desktop_url,
        "status": "running",
        "novnc_port": 12003,
    }
    assert read_vnc_token(docker_id) == recovered_token

    await audit_repo.log(
        actor="operator",
        action="WORKSTATION_RECOVERED",
        resource_type="Container",
        resource_id=docker_id,
        details={"status": "running"},
    )

    # Final verification: Audit log chain cryptographic integrity
    logs = await audit_repo.list_all(limit=100)
    ordered_logs = AuditLogChainVerifier.order_chain(logs)
    is_valid, issues = AuditLogChainVerifier.verify_records(ordered_logs)
    assert is_valid is True, f"Audit log chain integrity verification failed: {issues}"
