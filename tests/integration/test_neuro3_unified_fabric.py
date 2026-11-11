"""
ANCOS NEURO-3: Integration Test Suite for Unified Execution Fabric.
Tests end-to-end execution across all 5 modes, capability policy defenses,
cancellation endpoints, and deliverable verification receipts.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.main import app
from neural.constants import EmployeeStatus, ExecutionMode, TaskStatus
from neural.db.models import Employee, Project, Task, TaskAttempt
from neural.db.repositories import AuditRepository, EmployeeRepository, TaskRepository
from neural.exceptions import CapabilityPolicyViolationError, UnauthorizedToolError
from neural.execution.contracts import CapabilityPolicyEnforcer
from neural.execution.fabric import UnifiedExecutionFabric


@pytest.fixture
def test_workspace():
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir


@pytest.mark.asyncio
async def test_unified_fabric_runs_all_five_execution_modes(async_session, test_workspace):
    """Integrate UnifiedExecutionFabric running tasks across all 5 modes to deliverable verification."""
    task_repo = TaskRepository(async_session)
    employee_repo = EmployeeRepository(async_session)
    audit_repo = AuditRepository(async_session)
    fabric = UnifiedExecutionFabric(
        task_repo=task_repo,
        employee_repo=employee_repo,
        audit_repo=audit_repo,
    )

    modes = [
        ExecutionMode.TERMINAL_ONLY,
        ExecutionMode.GUI_ONLY,
        ExecutionMode.HYBRID,
        ExecutionMode.AUTHORIZED_TOOLS,
        ExecutionMode.RESEARCH_REASONING,
    ]

    for mode in modes:
        task = Task(
            task_id=f"INT-TASK-{mode.value.upper()}",
            project_id="PROJ-INT-01",
            title=f"Integration task for {mode.value}",
            objective=f"Execute {mode.value} task to verification receipt.",
            scope="Unified fabric integration",
            acceptance_criteria="Automated validation passes",
            execution_mode=mode,
            status=TaskStatus.RUNNING,
            fencing_token=1,
            required_capabilities=["python", "verification"],
        )

        sub_ws = os.path.join(test_workspace, mode.value)
        os.makedirs(sub_ws, exist_ok=True)

        res = await fabric.execute_task_with_contract(
            task=task,
            workspace_path=sub_ws,
            context={"authorized_tools": ["workspace_analyzer"]},
        )

        assert res["status"] == "completed"
        assert res["passed"] is True
        assert res["delta"]["complete"] is True
        assert "sha256_delta_digest" in res["delta"]
        assert len(res["delta"]["sha256_delta_digest"]) == 64
        assert res["verification_receipt"]["passed"] is True
        assert "signature" in res["verification_receipt"]


@pytest.mark.asyncio
async def test_capability_policy_defenses_integration():
    """Verify that mode boundaries fail closed against unauthorized shortcuts across all modes."""
    enforcer = CapabilityPolicyEnforcer()

    # 1. Terminal task blocks GUI action
    with pytest.raises(CapabilityPolicyViolationError):
        enforcer.validate_action(ExecutionMode.TERMINAL_ONLY, "gui_screenshot")

    # 2. GUI task blocks terminal CLI
    with pytest.raises(CapabilityPolicyViolationError):
        enforcer.validate_action(ExecutionMode.GUI_ONLY, "terminal_command", {"command": ["python3", "hack.py"]})

    # 3. Authorized tools blocks unlisted tool
    with pytest.raises(UnauthorizedToolError):
        enforcer.validate_action(
            ExecutionMode.AUTHORIZED_TOOLS,
            "tool_invocation",
            {"tool_name": "unknown_tool"},
            task_context={"authorized_tools": ["safe_tool"]},
        )

    # 4. Research reasoning blocks container state mutation
    with pytest.raises(CapabilityPolicyViolationError):
        enforcer.validate_action(ExecutionMode.RESEARCH_REASONING, "exec_shell", {"command": "drop database"})


@pytest.mark.asyncio
async def test_cancel_task_endpoint_integration(async_session):
    """Verify POST /tasks/{task_id}/cancel route cleanly invalidates attempts and increments fence."""
    # 1. Seed database with project, employee, task, and active attempt
    proj = Project(
        id="PROJ-CANCEL-API-01",
        name="Cancellation Test Project",
        objective="Verify cancel endpoint",
        status="active",
        execution_plan={},
    )
    emp = Employee(
        employee_id="ANC-EMP-API-CANCEL-01",
        name="API Cancel Employee",
        title="Software Engineer",
        department="Engineering",
        system_prompt="Engineering prompt.",
        status=EmployeeStatus.WORKING,
    )
    async_session.add_all([proj, emp])
    await async_session.flush()

    task = Task(
        task_id="TASK-API-CANCEL-01",
        project_id=proj.id,
        title="API Cancel Task",
        objective="Test API cancellation route",
        scope="Testing API cancel",
        acceptance_criteria="Automated validation passes",
        execution_mode=ExecutionMode.TERMINAL_ONLY,
        status=TaskStatus.RUNNING,
        assigned_to=emp.id,
        fencing_token=3,
    )
    async_session.add(task)
    await async_session.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-api-01",
        fencing_token=3,
        status="running",
        claimed_at=now,
        lease_expires_at=now + timedelta(seconds=60),
    )
    async_session.add(attempt)
    await async_session.commit()

    # 2. Invoke cancel endpoint via ASGI test client
    from neural.api.deps import get_db_session, get_task_repo
    app.dependency_overrides[get_db_session] = lambda: async_session
    app.dependency_overrides[get_task_repo] = lambda: TaskRepository(async_session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/tasks/{task.task_id}/cancel",
                json={
                    "reason": "Control plane cancellation request",
                    "requeue": False,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["task_id"] == "TASK-API-CANCEL-01"
            assert body["status"] == "cancelled"
            assert body["fencing_token"] == 4
            assert body["old_fencing_token"] == 3
            assert body["attempts_invalidated"] == 1
    finally:
        app.dependency_overrides.clear()

    # 3. Verify attempt state in database
    await async_session.refresh(attempt)
    assert attempt.status == "cancelled"
    assert "Control plane" in attempt.error_message


@pytest.mark.asyncio
async def test_cancel_and_requeue_with_standby_employee_api(async_session):
    """Verify POST /tasks/{task_id}/cancel with requeue=True reassigns to standby employee."""
    proj = Project(
        id="PROJ-REQUEUE-API-01",
        name="Requeue Test Project",
        objective="Verify requeue endpoint",
        status="active",
        execution_plan={},
    )
    emp1 = Employee(
        employee_id="ANC-EMP-REQUEUE-PRI",
        name="Primary Employee",
        title="Junior Engineer",
        department="Engineering",
        system_prompt="Primary prompt.",
        status=EmployeeStatus.WORKING,
    )
    emp_standby = Employee(
        employee_id="ANC-EMP-REQUEUE-STB",
        name="Standby Employee",
        title="Senior Engineer",
        department="Engineering",
        system_prompt="Standby prompt.",
        status=EmployeeStatus.IDLE,
    )
    async_session.add_all([proj, emp1, emp_standby])
    await async_session.flush()

    task = Task(
        task_id="TASK-API-REQUEUE-01",
        project_id=proj.id,
        title="API Requeue Task",
        objective="Test API requeue route",
        scope="Testing API requeue",
        acceptance_criteria="Automated validation passes",
        execution_mode=ExecutionMode.TERMINAL_ONLY,
        status=TaskStatus.RUNNING,
        assigned_to=emp1.id,
        fencing_token=7,
    )
    async_session.add(task)
    await async_session.flush()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    attempt = TaskAttempt(
        task_id=task.id,
        attempt_number=1,
        worker_id="worker-api-01",
        fencing_token=7,
        status="running",
        claimed_at=now,
        lease_expires_at=now + timedelta(seconds=60),
    )
    async_session.add(attempt)
    await async_session.commit()

    # Invoke cancel with requeue and standby
    from neural.api.deps import get_db_session, get_task_repo
    app.dependency_overrides[get_db_session] = lambda: async_session
    app.dependency_overrides[get_task_repo] = lambda: TaskRepository(async_session)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/tasks/{task.task_id}/cancel",
                json={
                    "reason": "Primary employee timed out; reassigning to standby",
                    "requeue": True,
                    "standby_employee_id": emp_standby.employee_id,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["task_id"] == "TASK-API-REQUEUE-01"
            assert body["status"] == "assigned"
            assert body["fencing_token"] == 8
            assert body["requeued"] is True
            assert body["assigned_to"] == emp_standby.id
    finally:
        app.dependency_overrides.clear()

    # Verify employees status
    await async_session.refresh(emp1)
    await async_session.refresh(emp_standby)
    assert emp1.status == EmployeeStatus.IDLE
    assert emp_standby.status == EmployeeStatus.WORKING
