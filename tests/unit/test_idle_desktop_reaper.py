"""Regression tests for the demand-driven idle-workstation reaper.

The reaper exists to free a desktop slot for a waiting PENDING task when the
deployment is at capacity. It must NOT tear down an idle desktop while free
slots remain: doing so kills a workstation nobody is competing for and revokes
the VNC token an operator is watching.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from neural.constants import ContainerStatus, EmployeeStatus, TaskStatus
from neural.db.models import Container, Employee, ResourceLease, Task, utc_now
from neural.tasks.idle_desktop_reaper import reap_idle_desktops
from sqlalchemy.ext.asyncio import AsyncSession

POOL = "desktop:local:"


class _StubContainerManager:
    def __init__(self) -> None:
        self.terminated: list[str] = []

    async def terminate_desktop(self, docker_id: str, **_kwargs: Any) -> None:
        self.terminated.append(docker_id)


async def _seed_idle_employee_with_desktop(
    session: AsyncSession,
    employee_id: str,
    slot_id: int,
) -> str:
    docker_id = f"ancos-desktop-{employee_id.lower()}"
    container = Container(
        docker_id=docker_id,
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        workspace_path=f"/home/employee/{employee_id}",
    )
    session.add(container)
    await session.flush()
    session.add(
        Employee(
            employee_id=employee_id,
            name=f"Employee {employee_id}",
            title="Engineer",
            department="Engineering",
            system_prompt="test",
            model_config_data={},
            approved_tools=["terminal"],
            status=EmployeeStatus.IDLE,
            container_id=container.id,
            execution_capabilities=["terminal"],
        )
    )
    session.add(
        ResourceLease(
            resource_key=f"{POOL}slot-{slot_id}",
            employee_id=employee_id,
            session_id=docker_id,
            fencing_token=1,
            status="active",
            lease_expires_at=utc_now() + timedelta(hours=1),
        )
    )
    await session.commit()
    return docker_id


async def _seed_pending_task(session: AsyncSession) -> None:
    session.add(
        Task(
            task_id="TASK-PENDING-1",
            project_id="PROJ-REAPER-1",
            title="Queued work",
            objective="Wait for a free slot",
            scope="Reaper regression",
            acceptance_criteria="Task is dispatched",
            status=TaskStatus.PENDING,
            execution_mode="terminal",
        )
    )
    await session.commit()


def _repos(session: AsyncSession):
    from neural.db.repositories import (
        ContainerRepository,
        EmployeeRepository,
        ResourceLeaseRepository,
        TaskRepository,
    )

    return (
        TaskRepository(session),
        EmployeeRepository(session),
        ContainerRepository(session),
        ResourceLeaseRepository(session),
    )


@pytest.mark.asyncio
async def test_reaper_spares_idle_desktop_while_free_slots_remain(async_session, monkeypatch):
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "3")
    monkeypatch.setenv("ANCOS_RESOURCE_POOL_ID", "local")
    docker_id = await _seed_idle_employee_with_desktop(async_session, "ANC-EMP-IDLE-A", slot_id=1)
    await _seed_pending_task(async_session)

    task_repo, employee_repo, container_repo, lease_repo = _repos(async_session)
    manager = _StubContainerManager()

    reaped = await reap_idle_desktops(
        task_repo, employee_repo, container_repo, lease_repo, manager
    )

    assert reaped == []
    assert manager.terminated == []
    active = await lease_repo.list_active()
    assert len(active) == 1
    assert active[0]["status"] == "active"
    assert docker_id  # sanity: the seeded workstation is the one we expected


@pytest.mark.asyncio
async def test_reaper_terminates_idle_desktop_when_slots_are_exhausted(async_session, monkeypatch):
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "1")
    monkeypatch.setenv("ANCOS_RESOURCE_POOL_ID", "local")
    await _seed_idle_employee_with_desktop(async_session, "ANC-EMP-IDLE-B", slot_id=1)
    await _seed_pending_task(async_session)

    task_repo, employee_repo, container_repo, lease_repo = _repos(async_session)
    manager = _StubContainerManager()

    reaped = await reap_idle_desktops(
        task_repo, employee_repo, container_repo, lease_repo, manager
    )

    assert reaped == ["ANC-EMP-IDLE-B"]
    assert manager.terminated == ["ancos-desktop-anc-emp-idle-b"]
