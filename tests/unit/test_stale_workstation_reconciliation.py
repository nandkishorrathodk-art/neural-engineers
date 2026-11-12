from __future__ import annotations

from types import SimpleNamespace

import pytest
from neural.constants import ContainerStatus
from neural.containers.manager import ContainerManager, DockerBrokerRequestError


class FakeContainerRepo:
    def __init__(self, rows):
        self._rows = rows
        self.status_updates: list[tuple[str, ContainerStatus]] = []

    async def list_all(self):
        return list(self._rows)

    async def update_status(self, docker_id: str, status: ContainerStatus) -> None:
        self.status_updates.append((docker_id, status))
        for row in self._rows:
            if row.docker_id == docker_id:
                row.status = status


def _row(docker_id: str, status: ContainerStatus = ContainerStatus.RUNNING):
    return SimpleNamespace(docker_id=docker_id, status=status)


def _manager_with_broker(monkeypatch, handler):
    manager = ContainerManager()
    manager.settings.DOCKER_BROKER_URL = "http://broker.local"
    manager.settings.DOCKER_BROKER_TOKEN = "token"
    monkeypatch.setattr(manager, "_broker_request", handler)
    return manager


@pytest.mark.asyncio
async def test_missing_workstation_row_is_failed_so_capacity_is_reclaimed(monkeypatch):
    """A RUNNING row whose container the broker reports absent must be demoted.

    Without this, a wiped daemon leaks desktop capacity forever: admission counts
    the phantom row as active and the reaper can never clear it.
    """
    stale = _row("ancos-desktop-anc-emp-dead", ContainerStatus.RUNNING)
    live = _row("ancos-desktop-anc-emp-live", ContainerStatus.RUNNING)
    repo = FakeContainerRepo([stale, live])

    async def handler(method, path, **kwargs):
        if path.endswith("ancos-desktop-anc-emp-live"):
            return {"status": "running"}
        raise DockerBrokerRequestError("Restricted Docker broker rejected operation: not found", status_code=404)

    manager = _manager_with_broker(monkeypatch, handler)
    manager._containers["ancos-desktop-anc-emp-dead"] = {"employee_id": "ANC-EMP-DEAD", "status": "running"}

    await manager._reconcile_stale_workstations(repo, keep_docker_id="ancos-desktop-anc-emp-new")

    assert repo.status_updates == [("ancos-desktop-anc-emp-dead", ContainerStatus.FAILED)]
    assert stale.status == ContainerStatus.FAILED
    assert live.status == ContainerStatus.RUNNING
    assert "ancos-desktop-anc-emp-dead" not in manager._containers


@pytest.mark.asyncio
async def test_uncertain_broker_reply_never_releases_capacity(monkeypatch):
    """A non-404 broker error must not demote a row; capacity is never granted on doubt."""
    row = _row("ancos-desktop-anc-emp-maybe", ContainerStatus.RUNNING)
    repo = FakeContainerRepo([row])

    async def handler(method, path, **kwargs):
        raise DockerBrokerRequestError("Restricted Docker broker rejected operation: upstream", status_code=503)

    manager = _manager_with_broker(monkeypatch, handler)

    await manager._reconcile_stale_workstations(repo, keep_docker_id="ancos-desktop-anc-emp-new")

    assert repo.status_updates == []
    assert row.status == ContainerStatus.RUNNING


@pytest.mark.asyncio
async def test_reconciliation_skips_target_and_non_desktop_rows(monkeypatch):
    target = _row("ancos-desktop-anc-emp-new", ContainerStatus.RUNNING)
    other = _row("ancos-some-other-container", ContainerStatus.RUNNING)
    repo = FakeContainerRepo([target, other])

    async def handler(method, path, **kwargs):
        raise AssertionError("no candidate should be probed")

    manager = _manager_with_broker(monkeypatch, handler)

    await manager._reconcile_stale_workstations(repo, keep_docker_id="ancos-desktop-anc-emp-new")

    assert repo.status_updates == []
