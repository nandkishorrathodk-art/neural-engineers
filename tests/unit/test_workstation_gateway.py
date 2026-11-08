from __future__ import annotations

import stat

import pytest
from fastapi import HTTPException
from neural.containers import broker_api


class MissingNetwork(Exception):
    pass


class Network:
    def __init__(self, employee_id: str, *, internal: bool = True):
        self.name = broker_api.employee_network_name(employee_id)
        self.id = f"net-{employee_id}"
        self.attrs = {
            "Internal": internal,
            "Labels": {"ancos.managed": "true", "ancos.employee_id": employee_id},
            "Containers": {},
        }
        self.connected = []
        self.disconnected = []
        self.removed = False

    def reload(self):
        pass

    def connect(self, container, aliases):
        self.connected.append((container, aliases))
        self.attrs["Containers"][container.id] = {}

    def disconnect(self, container):
        self.disconnected.append(container)
        self.attrs["Containers"].pop(container.id, None)

    def remove(self):
        self.removed = True


class Docker:
    def __init__(self, employee_id: str, *, network=None, gateway=True, health="healthy"):
        self.network = network
        self.gateway = (
            type(
                "Container",
                (),
                {
                    "id": "gateway-id",
                    "attrs": {
                        "State": {"Status": "running", "Health": {"Status": health}}
                    },
                    "reload": lambda _self: None,
                },
            )()
            if gateway
            else None
        )
        self.created = []

        class Networks:
            def get(inner, _name):
                if self.network is None:
                    raise MissingNetwork()
                return self.network

            def create(inner, name, **kwargs):
                self.created.append((name, kwargs))
                self.network = Network(employee_id)
                return self.network

        class Containers:
            def get(inner, _name):
                if self.gateway is None:
                    raise MissingNetwork()
                return self.gateway

        self.networks = Networks()
        self.containers = Containers()


def test_employee_network_is_created_internal_and_gateway_attached(monkeypatch):
    employee_id = "ANC-EMP-GW-1"
    monkeypatch.setattr(broker_api, "NotFound", MissingNetwork)
    monkeypatch.setattr(broker_api, "APIError", RuntimeError)
    monkeypatch.setattr(broker_api.settings, "EMPLOYEE_NETWORK", "test-employee")
    client = Docker(employee_id)

    network = broker_api.ensure_network(client, employee_id)

    assert network.attrs["Internal"] is True
    assert client.created[0][1]["internal"] is True
    assert client.created[0][1]["labels"]["ancos.employee_id"] == employee_id
    assert network.connected[0][1] == ["ancos-egress-gateway"]


def test_employee_network_rejects_unmanaged_or_non_internal_network(monkeypatch):
    employee_id = "ANC-EMP-GW-2"
    monkeypatch.setattr(broker_api, "NotFound", MissingNetwork)
    monkeypatch.setattr(broker_api.settings, "EMPLOYEE_NETWORK", "test-employee")
    network = Network(employee_id, internal=False)
    client = Docker(employee_id, network=network)

    with pytest.raises(HTTPException) as rejected:
        broker_api.ensure_network(client, employee_id)

    assert rejected.value.status_code == 409
    assert not network.connected


def test_employee_network_fails_closed_when_gateway_is_missing(monkeypatch):
    employee_id = "ANC-EMP-GW-3"
    monkeypatch.setattr(broker_api, "NotFound", MissingNetwork)
    monkeypatch.setattr(broker_api.settings, "EMPLOYEE_NETWORK", "test-employee")
    client = Docker(employee_id, network=Network(employee_id), gateway=False)

    with pytest.raises(HTTPException) as rejected:
        broker_api.ensure_network(client, employee_id)

    assert rejected.value.status_code == 503


def test_employee_network_fails_closed_when_gateway_is_unhealthy(monkeypatch):
    employee_id = "ANC-EMP-GW-4"
    monkeypatch.setattr(broker_api, "NotFound", MissingNetwork)
    monkeypatch.setattr(broker_api.settings, "EMPLOYEE_NETWORK", "test-employee")
    network = Network(employee_id)
    client = Docker(employee_id, network=network, health="unhealthy")

    with pytest.raises(HTTPException) as rejected:
        broker_api.ensure_network(client, employee_id)

    assert rejected.value.status_code == 503
    assert rejected.value.detail == "Workstation gateway is not healthy."
    assert not network.connected


def test_token_file_scopes_mapping_and_removes_it_on_revocation(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_api.settings, "VNC_TOKEN_DIR", str(tmp_path))
    one = broker_api.write_vnc_token("ancos-desktop-anc-emp-one")
    two = broker_api.write_vnc_token("ancos-desktop-anc-emp-two")
    path = tmp_path / "tokens"

    assert broker_api.read_vnc_token("ancos-desktop-anc-emp-one") == one
    assert broker_api.read_vnc_token("ancos-desktop-anc-emp-two") == two
    assert stat.S_IMODE(path.stat().st_mode) == 0o444

    broker_api.revoke_vnc_token("ancos-desktop-anc-emp-one")

    assert broker_api.read_vnc_token("ancos-desktop-anc-emp-one") is None
    assert broker_api.read_vnc_token("ancos-desktop-anc-emp-two") == two


def test_gateway_token_mapping_routes_to_internal_vnc_only(tmp_path, monkeypatch):
    monkeypatch.setattr(broker_api.settings, "VNC_TOKEN_DIR", str(tmp_path))
    token = broker_api.write_vnc_token("ancos-desktop-anc-emp-one")

    assert (tmp_path / "tokens").read_text(encoding="utf-8") == (
        f"{token}: ancos-desktop-anc-emp-one:5901\n"
    )
