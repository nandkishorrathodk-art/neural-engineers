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
    internal_network = type(
        "Network",
        (),
        {
            "attrs": {
                "Internal": True,
                "Containers": {},
                "Labels": {
                    "ancos.managed": "true",
                    "ancos.employee_id": "ANC-EMP-CAPACITY-TEST",
                },
            },
            "connect": lambda *_args, **_kwargs: None,
        },
    )()
    gateway = type(
        "Container",
        (),
        {
            "id": "gateway-id",
            "attrs": {"State": {"Status": "running", "Health": {"Status": "healthy"}}},
        },
    )()

    class FakeNetworks:
        def get(self, name):
            raise MissingNetwork(name)

        def create(self, name, **kwargs):
            created.append((name, kwargs))
            return internal_network

    class FakeDockerClient:
        networks = FakeNetworks()

        class containers:
            @staticmethod
            def get(_name):
                return gateway

    monkeypatch.setattr(broker_api, "NotFound", MissingNetwork)
    monkeypatch.setattr(broker_api.settings, "EMPLOYEE_NETWORK", "isolated-neuro-test-net")

    broker_api.ensure_network(FakeDockerClient(), "ANC-EMP-CAPACITY-TEST")

    assert created == [
        (
            "isolated-neuro-test-net-anc-emp-capacity-test",
            {
                "driver": "bridge",
                "internal": True,
                "labels": {"ancos.managed": "true", "ancos.employee_id": "ANC-EMP-CAPACITY-TEST"},
                "check_duplicate": True,
            },
        )
    ]
    assert broker_api.employee_network_name("ANC-EMP-CAPACITY-TEST") == (
        "isolated-neuro-test-net-anc-emp-capacity-test"
    )


def test_broker_refuses_existing_non_internal_employee_network(monkeypatch):
    class FakeNetworks:
        def get(self, _name):
            return type(
                "Network",
                (),
                {"attrs": {"Internal": False, "Labels": {"ancos.managed": "true", "ancos.employee_id": "ANC-EMP-X"}}},
            )()

    class FakeDockerClient:
        networks = FakeNetworks()

    monkeypatch.setattr(broker_api.settings, "EMPLOYEE_NETWORK", "unsafe-existing-net")
    with pytest.raises(HTTPException) as rejected:
        broker_api.ensure_network(FakeDockerClient(), "ANC-EMP-X")

    assert rejected.value.status_code == 409
    assert rejected.value.detail == "Employee network must be Docker-internal; refusing workstation provisioning."


def test_broker_refuses_to_attach_to_an_unhealthy_gateway(monkeypatch):
    employee_id = "ANC-EMP-GW-DOWN"
    network = type(
        "Network",
        (),
        {
            "attrs": {
                "Internal": True,
                "Labels": {"ancos.managed": "true", "ancos.employee_id": employee_id},
                "Containers": {},
            },
        },
    )()
    gateway = type(
        "Container",
        (),
        {"id": "gateway-id", "attrs": {"State": {"Status": "running", "Health": {"Status": "unhealthy"}}}},
    )()

    class FakeDockerClient:
        class networks:
            @staticmethod
            def get(_name):
                return network

        class containers:
            @staticmethod
            def get(_name):
                return gateway

    monkeypatch.setattr(broker_api.settings, "EMPLOYEE_NETWORK", "isolated-neuro-test-net")
    with pytest.raises(HTTPException) as rejected:
        broker_api.ensure_network(FakeDockerClient(), employee_id)

    assert rejected.value.status_code == 503
    assert rejected.value.detail == "Workstation gateway is not healthy."


def test_vnc_tokens_are_scoped_to_container_and_revoked_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_api.settings, "VNC_TOKEN_DIR", str(tmp_path))
    token = broker_api.write_vnc_token("ancos-desktop-anc-emp-test")

    assert len(token) >= 40
    assert broker_api.read_vnc_token("ancos-desktop-anc-emp-test") == token
    assert broker_api.read_vnc_token("ancos-desktop-anc-emp-other") is None

    broker_api.revoke_vnc_token("ancos-desktop-anc-emp-test")
    assert broker_api.read_vnc_token("ancos-desktop-anc-emp-test") is None
    assert (tmp_path / "tokens").read_text(encoding="utf-8") == ""


def test_ensure_employee_image_tags_role_alias_from_existing_base():
    tagged: list[str] = []

    class FakeImages:
        def get(self, tag):
            if tag == "ancos/employee-base:latest":
                return type("Image", (), {"tag": lambda _self, t: tagged.append(t)})()
            raise broker_api.ImageNotFound(tag)

        def build(self, **_kwargs):
            raise AssertionError("base image already present; rebuild must not happen")

    client = type("DockerClient", (), {"images": FakeImages()})()

    broker_api.ensure_employee_image(client, "ancos/employee-qa:latest")

    assert tagged == ["ancos/employee-qa:latest"]


def test_ensure_employee_image_builds_base_then_tags_alias(tmp_path, monkeypatch):
    built: list[str] = []
    tagged: list[str] = []
    present: set[str] = set()

    source = tmp_path / "ancos-docker"
    (source / "configs").mkdir(parents=True)
    (source / "Dockerfile.employee-base").write_text("FROM scratch\n", encoding="utf-8")
    (source / "entrypoint-employee.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(broker_api, "BUILD_SOURCE_DIR", source)

    class FakeImages:
        def get(self, tag):
            if tag in present:
                return type("Image", (), {"tag": lambda _self, t: tagged.append(t)})()
            raise broker_api.ImageNotFound(tag)

        def build(self, tag=None, **_kwargs):
            built.append(tag)
            present.add(tag)
            return None, []

    client = type("DockerClient", (), {"images": FakeImages()})()

    broker_api.ensure_employee_image(client, "ancos/employee-frontend:latest")

    assert built == ["ancos/employee-base:latest"]
    assert tagged == ["ancos/employee-frontend:latest"]


def test_ensure_employee_image_rejects_unallowlisted_tag():
    client = type("DockerClient", (), {"images": object()})()

    with pytest.raises(HTTPException) as rejected:
        broker_api.ensure_employee_image(client, "ancos/employee-evil:latest")

    assert rejected.value.status_code == 400
    assert rejected.value.detail == "Requested employee image is not allowlisted."
