from __future__ import annotations

import pytest
from fastapi import HTTPException
from neural.containers import broker_api


def test_broker_admission_clamps_deployment_capacity_to_hardware_profile(monkeypatch):
    monkeypatch.setenv("ANCOS_MAX_ACTIVE_DESKTOPS", "8")

    class FakeContainers:
        def list(self, **_kwargs):
            return [
                type("Container", (), {"name": f"ancos-desktop-anc-emp-{index}"})()
                for index in range(4)
            ]

    class FakeDockerClient:
        containers = FakeContainers()

    monkeypatch.setattr(broker_api, "docker_client", lambda: FakeDockerClient())

    with pytest.raises(HTTPException) as rejected:
        broker_api._provision_single_container(
            broker_api.ProvisionRequest(
                employee_id="ANC-EMP-CAPACITY-TEST",
                image="ancos/employee-base:latest",
            )
        )

    assert rejected.value.status_code == 409
    assert rejected.value.detail == "Desktop capacity is full (4/4)."


def test_broker_uses_configured_employee_network(monkeypatch):
    class MissingNetwork(Exception):
        pass

    created = []

    class FakeNetworks:
        def get(self, name):
            raise MissingNetwork(name)

        def create(self, name, **kwargs):
            created.append((name, kwargs))

    class FakeDockerClient:
        networks = FakeNetworks()

    monkeypatch.setattr(broker_api, "NotFound", MissingNetwork)
    monkeypatch.setattr(broker_api.settings, "EMPLOYEE_NETWORK", "isolated-neuro-test-net")

    broker_api.ensure_network(FakeDockerClient())

    assert created == [
        (
            "isolated-neuro-test-net",
            {"driver": "bridge", "check_duplicate": True},
        )
    ]
