"""Tests for automated workstation provisioning.

These lock in the autonomy hand-off that was missing: an assigned task whose
employee has no workstation must cause a *pending* human approval to be raised,
and once a human approves it the coordinator provisions the desktop. The gate
itself must never be auto-approved.
"""

from __future__ import annotations

import pytest
from neural.constants import (
    ApprovalStatus,
    ContainerStatus,
    EmployeeStatus,
    ExecutionMode,
    TaskStatus,
)
from neural.db.repositories import (
    ApprovalRepository,
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    ProjectRepository,
    ResourceLeaseRepository,
    TaskRepository,
)
from neural.tasks.workstation_provisioning import (
    _open_provision_employee_ids,
    provision_approved_workstations,
    request_pending_workstation_approvals,
)


class _ContainerManagerStub:
    """Minimal stand-in for ContainerManager; no real broker or Docker calls."""

    def __init__(self, desktop: dict | None = None) -> None:
        self._desktop = desktop
        self.provision_calls: list[str] = []

    async def get_desktop_by_employee_id(self, employee_id, container_repo=None, employee_repo=None):
        return self._desktop

    async def provision_employee_desktop(
        self, employee_id, role_image="employee-base", container_repo=None, employee_repo=None
    ):
        self.provision_calls.append(employee_id)
        return {
            "docker_id": f"ancos-desktop-{employee_id.lower()}",
            "employee_id": employee_id,
            "image": role_image,
            "status": "running",
        }

    async def _broker_request(self, method, path):  # pragma: no cover - not exercised here
        raise AssertionError("broker must not be called when provisioning succeeds")


async def _seed_assigned_task(session):
    employee = await EmployeeRepository(session).create(
        employee_id="ANC-EMP-PROV-01",
        name="Provisioning Test",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    project = await ProjectRepository(session).create(
        name="Provisioning", objective="Provision a workstation for the assigned task"
    )
    task = await TaskRepository(session).create(
        task_id="TASK-PROV-01",
        project_id=project.id,
        assigned_to=employee.id,
        title="Build the deliverable",
        objective="Do the work",
        scope="Task workspace",
        acceptance_criteria="Tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
        resource_budget={"max_tokens": 500},
    )
    return employee, task


@pytest.mark.asyncio
async def test_request_raises_pending_approval_for_unprovisioned_assignee(async_session):
    employee, _ = await _seed_assigned_task(async_session)
    container_mgr = _ContainerManagerStub(desktop=None)

    requested = await request_pending_workstation_approvals(
        TaskRepository(async_session),
        EmployeeRepository(async_session),
        ApprovalRepository(async_session),
        AuditRepository(async_session),
        ResourceLeaseRepository(async_session),
        ContainerRepository(async_session),
        container_mgr,
    )
    assert requested == [employee.employee_id]

    pending = await ApprovalRepository(async_session).list_pending()
    assert len(pending) == 1
    assert pending[0].action_type == "PROVISION_WORKSTATION"
    assert pending[0].status == ApprovalStatus.PENDING
    assert pending[0].details == {"employee_id": employee.employee_id}


@pytest.mark.asyncio
async def test_request_is_idempotent_while_approval_open(async_session):
    await _seed_assigned_task(async_session)
    container_mgr = _ContainerManagerStub(desktop=None)
    args = (
        TaskRepository(async_session),
        EmployeeRepository(async_session),
        ApprovalRepository(async_session),
        AuditRepository(async_session),
        ResourceLeaseRepository(async_session),
        ContainerRepository(async_session),
        container_mgr,
    )
    first = await request_pending_workstation_approvals(*args)
    second = await request_pending_workstation_approvals(*args)
    assert first != []
    assert second == []
    assert len(await ApprovalRepository(async_session).list_pending()) == 1


@pytest.mark.asyncio
async def test_request_skips_when_desktop_already_running(async_session):
    await _seed_assigned_task(async_session)
    container_mgr = _ContainerManagerStub(desktop={"status": "running"})
    requested = await request_pending_workstation_approvals(
        TaskRepository(async_session),
        EmployeeRepository(async_session),
        ApprovalRepository(async_session),
        AuditRepository(async_session),
        ResourceLeaseRepository(async_session),
        ContainerRepository(async_session),
        container_mgr,
    )
    assert requested == []
    assert await ApprovalRepository(async_session).list_pending() == []


@pytest.mark.asyncio
async def test_approved_approval_is_provisioned_and_consumed(async_session):
    employee, _ = await _seed_assigned_task(async_session)
    container_mgr = _ContainerManagerStub(desktop=None)
    approval_repo = ApprovalRepository(async_session)

    await request_pending_workstation_approvals(
        TaskRepository(async_session),
        EmployeeRepository(async_session),
        approval_repo,
        AuditRepository(async_session),
        ResourceLeaseRepository(async_session),
        ContainerRepository(async_session),
        container_mgr,
    )
    approval = (await approval_repo.list_pending())[0]
    # A human (independent approver) says yes.
    decided = await approval_repo.decide(approval.id, "approved", "independent-approver")
    assert decided.status == ApprovalStatus.APPROVED

    provisioned = await provision_approved_workstations(
        approval_repo,
        EmployeeRepository(async_session),
        ContainerRepository(async_session),
        ResourceLeaseRepository(async_session),
        TaskRepository(async_session),
        container_mgr,
    )
    assert provisioned == [f"ancos-desktop-{employee.employee_id.lower()}"]
    assert container_mgr.provision_calls == [employee.employee_id]

    consumed = await approval_repo.get_by_id(approval.id)
    assert consumed.consumed_at is not None

    # A second pass must not provision again (approval already consumed).
    again = await provision_approved_workstations(
        approval_repo,
        EmployeeRepository(async_session),
        ContainerRepository(async_session),
        ResourceLeaseRepository(async_session),
        TaskRepository(async_session),
        container_mgr,
    )
    assert again == []
    assert container_mgr.provision_calls == [employee.employee_id]


@pytest.mark.asyncio
async def test_pending_approval_is_not_provisioned(async_session):
    await _seed_assigned_task(async_session)
    container_mgr = _ContainerManagerStub(desktop=None)
    approval_repo = ApprovalRepository(async_session)
    await request_pending_workstation_approvals(
        TaskRepository(async_session),
        EmployeeRepository(async_session),
        approval_repo,
        AuditRepository(async_session),
        ResourceLeaseRepository(async_session),
        ContainerRepository(async_session),
        container_mgr,
    )
    # No human decision yet -> must not provision.
    provisioned = await provision_approved_workstations(
        approval_repo,
        EmployeeRepository(async_session),
        ContainerRepository(async_session),
        ResourceLeaseRepository(async_session),
        TaskRepository(async_session),
        container_mgr,
    )
    assert provisioned == []
    assert container_mgr.provision_calls == []


def test_open_provision_employee_ids_matches_details_and_status():
    from neural.db.models import Approval

    def _approval(status, employee_id, consumed_at=None):
        return Approval(
            id="a",
            action_type="PROVISION_WORKSTATION",
            description="d",
            justification="j",
            requested_by="BossAI",
            details={"employee_id": employee_id},
            status=status,
            consumed_at=consumed_at,
        )

    open_pending = [_approval(ApprovalStatus.PENDING, "ANC-EMP-X")]
    assert _open_provision_employee_ids(open_pending) == {"ANC-EMP-X"}

    rejected = [_approval(ApprovalStatus.REJECTED, "ANC-EMP-X")]
    assert _open_provision_employee_ids(rejected) == set()


@pytest.mark.asyncio
async def test_provisioning_survives_lease_claim_session_rollback(async_session):
    """A lease claim for an already-leased employee rolls the session back.

    That expires every ORM object in the shared session, so any attribute read
    afterwards (``approval.id``, ``employee.employee_id``) raises
    ``MissingGreenlet``. The coordinator must snapshot primitives before the
    claim so an occupied slot merely defers, never crashes the cycle.
    """
    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-LEASE-01",
        name="Lease Rollback",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    approval_repo = ApprovalRepository(async_session)
    approval = await approval_repo.create(
        action_type="PROVISION_WORKSTATION",
        description="provision",
        justification="test",
        requested_by="BossAI",
        details={"employee_id": employee.employee_id},
        status=ApprovalStatus.APPROVED,
    )
    approval_id = approval.id
    # Occupy the employee's desktop slot so the next claim hits the rollback path.
    await ResourceLeaseRepository(async_session).claim(
        resource_key="desktop:local:slot-1",
        employee_id=employee.employee_id,
        session_id=f"ancos-desktop-{employee.employee_id.lower()}",
    )

    container_mgr = _ContainerManagerStub(desktop=None)
    provisioned = await provision_approved_workstations(
        approval_repo,
        EmployeeRepository(async_session),
        ContainerRepository(async_session),
        ResourceLeaseRepository(async_session),
        TaskRepository(async_session),
        container_mgr,
    )
    assert provisioned == []
    assert container_mgr.provision_calls == []
    # The approval must remain available for a later cycle once a slot frees up.
    assert (await approval_repo.get_by_id(approval_id)).consumed_at is None


async def _make_project_with_tasks(session, statuses):
    from neural.db.models import Task

    project = await ProjectRepository(session).create(name="P", objective="do work")
    for index, status in enumerate(statuses):
        session.add(
            Task(
                task_id=f"TASK-{index}",
                project_id=project.id,
                title=f"task {index}",
                objective="o",
                scope="s",
                acceptance_criteria="a",
                status=status,
            )
        )
    await session.commit()
    return project.id


@pytest.mark.asyncio
async def test_project_advances_to_completed_when_all_tasks_complete(async_session):
    from neural.constants import ProjectStatus

    project_id = await _make_project_with_tasks(
        async_session, [TaskStatus.COMPLETED, TaskStatus.COMPLETED]
    )
    transitions = await ProjectRepository(async_session).reconcile_statuses()
    assert transitions == [{"project_id": project_id, "from": "planning", "to": "completed"}]
    project = await ProjectRepository(async_session).get_by_id(project_id)
    assert project.status == ProjectStatus.COMPLETED


@pytest.mark.asyncio
async def test_project_advances_to_active_while_tasks_in_flight(async_session):
    from neural.constants import ProjectStatus

    project_id = await _make_project_with_tasks(
        async_session, [TaskStatus.COMPLETED, TaskStatus.RUNNING]
    )
    await ProjectRepository(async_session).reconcile_statuses()
    project = await ProjectRepository(async_session).get_by_id(project_id)
    assert project.status == ProjectStatus.ACTIVE


@pytest.mark.asyncio
async def test_project_marks_failed_when_a_task_failed(async_session):
    from neural.constants import ProjectStatus

    project_id = await _make_project_with_tasks(
        async_session, [TaskStatus.COMPLETED, TaskStatus.FAILED]
    )
    await ProjectRepository(async_session).reconcile_statuses()
    project = await ProjectRepository(async_session).get_by_id(project_id)
    assert project.status == ProjectStatus.FAILED


@pytest.mark.asyncio
async def test_project_stays_planning_until_any_task_completes(async_session):
    from neural.constants import ProjectStatus

    project_id = await _make_project_with_tasks(
        async_session, [TaskStatus.PENDING, TaskStatus.PENDING]
    )
    transitions = await ProjectRepository(async_session).reconcile_statuses()
    assert transitions == []
    project = await ProjectRepository(async_session).get_by_id(project_id)
    assert project.status == ProjectStatus.PLANNING


class _ReaperContainerManagerStub:
    def __init__(self) -> None:
        self.terminated: list[str] = []

    async def terminate_desktop(self, docker_id, container_repo=None):
        self.terminated.append(docker_id)


async def _seed_idle_desktop(session, employee_id, docker_id, status=TaskStatus.PENDING):
    from neural.db.models import Task

    employee = await EmployeeRepository(session).create(
        employee_id=employee_id,
        name="Idle Worker",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(session).create(
        docker_id=docker_id,
        image="employee-base",
        status=ContainerStatus.RUNNING,
        workspace_path=f"/home/employee/{employee_id}",
    )
    employee.container_id = container.id
    await session.commit()
    project = await ProjectRepository(session).create(name="P", objective="o")
    session.add(
        Task(
            task_id=f"TASK-{employee_id}",
            project_id=project.id,
            assigned_to=employee.id,
            title="t",
            objective="o",
            scope="s",
            acceptance_criteria="a",
            status=status,
        )
    )
    await session.commit()
    return employee, container


@pytest.mark.asyncio
async def test_reaper_terminates_idle_desktop_when_backlog_exists(async_session, monkeypatch):
    from neural.db.models import Container, Task
    from neural.tasks.idle_desktop_reaper import reap_idle_desktops

    # Reaping only happens under real slot pressure: a single desktop slot with
    # a waiting PENDING task, so the idle holder is the only thing blocking it.
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "1")
    monkeypatch.setenv("ANCOS_RESOURCE_POOL_ID", "local")

    # Idle employee holds the running desktop (its only task is COMPLETED)...
    idle_emp, idle_container = await _seed_idle_desktop(
        async_session, "ANC-EMP-IDLE-01", "ancos-desktop-anc-emp-idle-01", TaskStatus.COMPLETED
    )
    lease_repo = ResourceLeaseRepository(async_session)
    await lease_repo.claim(
        resource_key="desktop:local:slot-1",
        employee_id=idle_emp.employee_id,
        session_id="ancos-desktop-anc-emp-idle-01",
    )
    # ...while another employee has a PENDING task waiting for a slot.
    project = await ProjectRepository(async_session).create(name="Q", objective="o")
    async_session.add(
        Task(
            task_id="TASK-WAITING",
            project_id=project.id,
            title="waiting",
            objective="o",
            scope="s",
            acceptance_criteria="a",
            status=TaskStatus.PENDING,
        )
    )
    await async_session.commit()

    mgr = _ReaperContainerManagerStub()
    reaped = await reap_idle_desktops(
        TaskRepository(async_session),
        EmployeeRepository(async_session),
        ContainerRepository(async_session),
        lease_repo,
        mgr,
    )
    assert reaped == [idle_emp.employee_id]
    assert mgr.terminated == ["ancos-desktop-anc-emp-idle-01"]
    container = await async_session.get(Container, idle_container.id)
    assert container.status == ContainerStatus.STOPPED
    assert await lease_repo.list_active() == []


@pytest.mark.asyncio
async def test_reaper_keeps_desktop_for_employee_with_active_task(async_session):
    from neural.tasks.idle_desktop_reaper import reap_idle_desktops

    busy_emp, _ = await _seed_idle_desktop(
        async_session, "ANC-EMP-BUSY-01", "ancos-desktop-anc-emp-busy-01", TaskStatus.RUNNING
    )
    lease_repo = ResourceLeaseRepository(async_session)
    await lease_repo.claim(
        resource_key="desktop:local:slot-1",
        employee_id=busy_emp.employee_id,
        session_id="ancos-desktop-anc-emp-busy-01",
    )
    project = await ProjectRepository(async_session).create(name="Q", objective="o")
    from neural.db.models import Task

    async_session.add(
        Task(
            task_id="TASK-WAITING-2",
            project_id=project.id,
            title="waiting",
            objective="o",
            scope="s",
            acceptance_criteria="a",
            status=TaskStatus.PENDING,
        )
    )
    await async_session.commit()

    mgr = _ReaperContainerManagerStub()
    reaped = await reap_idle_desktops(
        TaskRepository(async_session),
        EmployeeRepository(async_session),
        ContainerRepository(async_session),
        lease_repo,
        mgr,
    )
    assert reaped == []
    assert mgr.terminated == []


@pytest.mark.asyncio
async def test_reaper_noop_without_backlog(async_session):
    from neural.tasks.idle_desktop_reaper import reap_idle_desktops

    idle_emp, _ = await _seed_idle_desktop(
        async_session, "ANC-EMP-IDLE-02", "ancos-desktop-anc-emp-idle-02", TaskStatus.COMPLETED
    )
    lease_repo = ResourceLeaseRepository(async_session)
    await lease_repo.claim(
        resource_key="desktop:local:slot-1",
        employee_id=idle_emp.employee_id,
        session_id="ancos-desktop-anc-emp-idle-02",
    )
    mgr = _ReaperContainerManagerStub()
    reaped = await reap_idle_desktops(
        TaskRepository(async_session),
        EmployeeRepository(async_session),
        ContainerRepository(async_session),
        lease_repo,
        mgr,
    )
    assert reaped == []
    assert mgr.terminated == []


async def _seed_dag_employee(session, employee_id, docker_id, skill, container_status=ContainerStatus.RUNNING):
    employee = await EmployeeRepository(session).create(
        employee_id=employee_id,
        name="DAG Worker",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
        execution_capabilities=["terminal"],
    )
    container = await ContainerRepository(session).create(
        docker_id=docker_id,
        image="employee-base",
        status=container_status,
        workspace_path=f"/home/employee/{employee_id}",
    )
    employee.container_id = container.id
    await EmployeeRepository(session).add_skill(employee_id, skill, level="expert", verified=True)
    await session.commit()
    return employee


async def _seed_chain(session, skill):
    """A -> B -> C chain where only A is COMPLETED with a passing receipt."""

    project = await ProjectRepository(session).create(name="Chain", objective="o")
    repo = TaskRepository(session)
    tasks = {}
    for name in ("A", "B", "C"):
        tasks[name] = await repo.create(
            task_id=f"TASK-CHAIN-{name}",
            project_id=project.id,
            title=f"task {name}",
            objective="o",
            scope="s",
            acceptance_criteria="a",
            execution_mode=ExecutionMode.TERMINAL,
            required_capabilities=[skill],
            status=TaskStatus.PENDING,
        )
    await repo.add_dependency("TASK-CHAIN-B", "TASK-CHAIN-A")
    await repo.add_dependency("TASK-CHAIN-C", "TASK-CHAIN-B")
    await repo.complete_with_receipt("TASK-CHAIN-A", {"passed": True, "notes": "ok"})
    return project, tasks


@pytest.mark.asyncio
async def test_dispatch_assigns_only_dependency_ready_task(async_session):
    emp = await _seed_dag_employee(
        async_session, "ANC-EMP-DAG-01", "ancos-desktop-anc-emp-dag-01", "Python"
    )
    await _seed_chain(async_session, "Python")

    assigned = await TaskRepository(async_session)._dispatch_pending_tasks()
    assert assigned == ["TASK-CHAIN-B"], "only the dependency-ready successor may be assigned"
    b = await TaskRepository(async_session).get_by_task_id("TASK-CHAIN-B")
    assert b.status == TaskStatus.ASSIGNED
    assert b.assigned_to == emp.id
    c = await TaskRepository(async_session).get_by_task_id("TASK-CHAIN-C")
    assert c.status == TaskStatus.PENDING, "the grandchild must stay blocked"


@pytest.mark.asyncio
async def test_dispatch_skips_task_when_a_desktop_is_busy(async_session):
    # A running desktop exists (held by a busy employee); a task must not be
    # handed to a desktop-less employee, or an extra workstation would be
    # provisioned needlessly instead of reusing the running one.
    busy = await _seed_dag_employee(
        async_session, "ANC-EMP-DAG-02", "ancos-desktop-anc-emp-dag-02", "Python"
    )
    busy.status = EmployeeStatus.WORKING
    await _seed_dag_employee(
        async_session,
        "ANC-EMP-DAG-02B",
        "ancos-desktop-anc-emp-dag-02b",
        "Python",
        ContainerStatus.PROVISIONING,
    )
    await async_session.commit()
    await _seed_chain(async_session, "Python")

    assigned = await TaskRepository(async_session)._dispatch_pending_tasks()
    assert assigned == [], "a busy running desktop must be reused, not duplicated"

@pytest.mark.asyncio
async def test_dispatch_falls_back_when_only_running_desktop_is_skill_incompatible(async_session):
    """A running desktop that cannot take the task must not stall provisioning.

    The dispatcher only skips the desktop-less fallback when an *idle, capable*
    desktop is running. If the single running desktop belongs to an employee who
    cannot run the task (skill mismatch) or is busy, that desktop can never take
    it, so falling back to an idle desktop-less employee is the only way the
    task's provisioning approval is ever raised. Without the fallback the task is
    stranded in PENDING forever -- the exact defect this test locks out.
    """
    # Running desktop, but the owner lacks the required skill.
    await _seed_dag_employee(
        async_session, "ANC-EMP-DAG-02D", "ancos-desktop-anc-emp-dag-02d", "Rust"
    )
    # Idle, capable employee with no desktop: the only one that can do the work.
    capable = await _seed_dag_employee(
        async_session,
        "ANC-EMP-DAG-02E",
        "ancos-desktop-anc-emp-dag-02e",
        "Python",
        ContainerStatus.PROVISIONING,
    )
    await _seed_chain(async_session, "Python")

    assigned = await TaskRepository(async_session)._dispatch_pending_tasks()
    assert assigned == ["TASK-CHAIN-B"], (
        "an incompatible running desktop must not block the desktop-less fallback"
    )
    b = await TaskRepository(async_session).get_by_task_id("TASK-CHAIN-B")
    assert b.assigned_to == capable.id


@pytest.mark.asyncio
async def test_dispatch_falls_back_when_only_running_desktop_is_busy_but_skill_capable(async_session):
    """A busy *incompatible* running desktop must not stall a capable idle peer.

    A busy running desktop owned by a skill-incompatible employee can never take
    the task; a capable desktop-less employee must still be assigned so its
    provisioning approval can be raised.
    """
    busy = await _seed_dag_employee(
        async_session, "ANC-EMP-DAG-02F", "ancos-desktop-anc-emp-dag-02f", "Rust"
    )
    busy.status = EmployeeStatus.WORKING
    capable = await _seed_dag_employee(
        async_session,
        "ANC-EMP-DAG-02G",
        "ancos-desktop-anc-emp-dag-02g",
        "Python",
        ContainerStatus.PROVISIONING,
    )
    await async_session.commit()
    await _seed_chain(async_session, "Python")

    assigned = await TaskRepository(async_session)._dispatch_pending_tasks()
    assert assigned == ["TASK-CHAIN-B"]
    b = await TaskRepository(async_session).get_by_task_id("TASK-CHAIN-B")
    assert b.assigned_to == capable.id


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_desktop_less_employee_when_none_running(async_session):
    # No desktop is running anywhere: assigning the dependency-ready task is the
    # only way its provisioning approval can ever be raised, so the dispatcher
    # must fall back to an idle, capable employee rather than deadlocking.
    await _seed_dag_employee(
        async_session,
        "ANC-EMP-DAG-02C",
        "ancos-desktop-anc-emp-dag-02c",
        "Python",
        ContainerStatus.PROVISIONING,
    )
    await _seed_chain(async_session, "Python")

    assigned = await TaskRepository(async_session)._dispatch_pending_tasks()
    assert assigned == ["TASK-CHAIN-B"], "the chain must not deadlock with zero desktops"


@pytest.mark.asyncio
async def test_dispatch_requires_passing_dependency_receipt(async_session):
    await _seed_dag_employee(
        async_session, "ANC-EMP-DAG-03", "ancos-desktop-anc-emp-dag-03", "Python"
    )
    project = await ProjectRepository(async_session).create(name="Chain2", objective="o")
    repo = TaskRepository(async_session)
    await repo.create(
        task_id="TASK-NORECEIPT-A",
        project_id=project.id,
        title="a",
        objective="o",
        scope="s",
        acceptance_criteria="a",
        execution_mode=ExecutionMode.TERMINAL,
        required_capabilities=["Python"],
        status=TaskStatus.COMPLETED,  # completed but no verification receipt
    )
    await repo.create(
        task_id="TASK-NORECEIPT-B",
        project_id=project.id,
        title="b",
        objective="o",
        scope="s",
        acceptance_criteria="a",
        execution_mode=ExecutionMode.TERMINAL,
        required_capabilities=["Python"],
        status=TaskStatus.PENDING,
    )
    await repo.add_dependency("TASK-NORECEIPT-B", "TASK-NORECEIPT-A")

    assigned = await TaskRepository(async_session)._dispatch_pending_tasks()
    assert assigned == []
    b = await repo.get_by_task_id("TASK-NORECEIPT-B")
    assert b.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_dispatch_one_task_per_idle_employee(async_session):
    await _seed_dag_employee(
        async_session, "ANC-EMP-DAG-04", "ancos-desktop-anc-emp-dag-04", "Python"
    )
    await _seed_dag_employee(
        async_session, "ANC-EMP-DAG-05", "ancos-desktop-anc-emp-dag-05", "Python"
    )
    project = await ProjectRepository(async_session).create(name="Parallel", objective="o")
    repo = TaskRepository(async_session)
    for name in ("P1", "P2"):
        await repo.create(
            task_id=f"TASK-PAR-{name}",
            project_id=project.id,
            title=name,
            objective="o",
            scope="s",
            acceptance_criteria="a",
            execution_mode=ExecutionMode.TERMINAL,
            required_capabilities=["Python"],
            status=TaskStatus.PENDING,
        )

    assigned = await TaskRepository(async_session)._dispatch_pending_tasks()
    assert len(assigned) == 2, "two idle employees with running desktops take two ready tasks"
    owners = {(await repo.get_by_task_id(tid)).assigned_to for tid in assigned}
    assert len(owners) == 2, "each employee takes at most one task"


@pytest.mark.asyncio
async def test_claim_recovers_task_pinned_by_stale_submitted_attempt(async_session):
    """A leftover 'submitted' attempt from a dead generation must not pin a task.

    The task's fencing token was superseded (as happens when the stale-recovery
    loop requeues and re-dispatches a task), so its old submitted attempt can
    never receive a verification receipt. The task must become claimable again
    instead of waiting forever for a receipt that will never come.
    """
    from datetime import timedelta

    from neural.db.models import TaskAttempt
    from neural.db.repositories import utc_now

    emp = await _seed_dag_employee(
        async_session, "ANC-EMP-PIN-01", "ancos-desktop-anc-emp-pin-01", "Python"
    )
    project = await ProjectRepository(async_session).create(name="Pinned", objective="o")
    repo = TaskRepository(async_session)
    task = await repo.create(
        task_id="TASK-PINNED",
        project_id=project.id,
        title="t",
        objective="o",
        scope="s",
        acceptance_criteria="a",
        execution_mode=ExecutionMode.TERMINAL,
        required_capabilities=["Python"],
        status=TaskStatus.ASSIGNED,
    )
    task.assigned_to = emp.id
    task.fencing_token = 178
    async_session.add(
        TaskAttempt(
            task_id=task.id,
            attempt_number=1,
            worker_id="terminal-worker",
            fencing_token=4,  # superseded: cannot match task.fencing_token
            status="submitted",
            lease_expires_at=utc_now() - timedelta(hours=1),
        )
    )
    await async_session.commit()

    claimed = await TaskRepository(async_session).claim_next_task("terminal-worker")
    assert claimed is not None, "a stale submitted attempt must not pin the task"
    assert claimed["task_id"] == "TASK-PINNED"
    assert claimed["employee_id"] == emp.employee_id
