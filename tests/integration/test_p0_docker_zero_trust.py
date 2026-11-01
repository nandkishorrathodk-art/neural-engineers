from __future__ import annotations

import os
import shutil

import pytest
from neural.containers.broker import RestrictedDockerBroker
from neural.containers.manager import ContainerManager
from neural.exceptions import SecurityViolationError


def test_broker_rejects_unauthorized_container_names():
    """Validates that broker restricts control strictly to employee containers."""
    broker = RestrictedDockerBroker(workspace_base="./data/workspaces")

    # Unauthorized targets must be rejected
    rogue_targets = [
        "ancos-core-api",
        "ancos-redis",
        "docker-daemon",
        "host-container",
        "evil-box",
        "ancos-desktop-root",
    ]
    for target in rogue_targets:
        with pytest.raises(SecurityViolationError, match="Unauthorized container target"):
            broker.validate_container_id(target)

    # Valid employee container names must pass
    broker.validate_container_id("ancos-desktop-anc-emp-0001")
    broker.validate_container_id("ancos-desktop-anc-emp-abcd")
    broker.validate_container_id("ancos-desktop-anc-emp-unit-test")


def test_broker_blocks_host_socket_and_root_mounts():
    """Validates that broker strictly prohibits mounting docker.sock or host system paths."""
    broker = RestrictedDockerBroker(workspace_base="./data/workspaces")

    # Attempt mounting /var/run/docker.sock
    with pytest.raises(SecurityViolationError, match="cannot mount host system path"):
        broker.validate_volume_mounts({
            "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}
        })

    # Attempt mounting host root /
    with pytest.raises(SecurityViolationError, match="cannot mount host system path"):
        broker.validate_volume_mounts({
            "/": {"bind": "/home/employee/workspace", "mode": "rw"}
        })

    # Attempt mounting path outside workspace base
    with pytest.raises(SecurityViolationError, match="outside designated workspace base"):
        broker.validate_volume_mounts({
            "/tmp/arbitrary_dir": {"bind": "/home/employee/workspace", "mode": "rw"}
        })

    # Prefix bypass attack: /data/workspaces_evil must NOT be treated as inside /data/workspaces
    with pytest.raises(SecurityViolationError, match="outside designated workspace base"):
        broker.validate_volume_mounts({
            os.path.abspath("./data/workspaces_evil/emp1"): {"bind": "/home/employee/workspace", "mode": "rw"}
        })

    # Directory traversal attack: /data/workspaces/../../etc
    with pytest.raises(SecurityViolationError):
        broker.validate_volume_mounts({
            "./data/workspaces/../../etc": {"bind": "/home/employee/workspace", "mode": "rw"}
        })


def test_broker_enforces_least_privilege_kwargs():
    """Validates automatic enforcement of dropped caps, non-root user, and resource limits."""
    broker = RestrictedDockerBroker(workspace_base="./data/workspaces")

    insecure_kwargs = {
        "name": "ancos-desktop-anc-emp-0042",
        "image": "ancos/employee-base:latest",
        "privileged": True,
        "user": "0:0",
        "cap_add": ["SYS_ADMIN"],
        "pids_limit": 5000,
        "volumes": {
            os.path.abspath("./data/workspaces/anc-emp-0042"): {"bind": "/home/employee/workspace", "mode": "rw"}
        }
    }

    hardened = broker.harden_container_kwargs(insecure_kwargs)

    assert hardened["privileged"] is False
    assert hardened["user"] == "1000:1000"
    assert hardened["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in hardened["security_opt"]
    assert hardened["pids_limit"] <= 200


@pytest.mark.asyncio
async def test_container_manager_provisions_with_broker_hardening():
    """Tests ContainerManager invoking broker to harden real workstation provisioning."""
    manager = ContainerManager()
    emp_id = "ANC-EMP-0099"

    spec = await manager.provision_employee_desktop(
        employee_id=emp_id,
        role_image="employee-swe",
        cpu_limit=1.5,
        mem_limit="2g"
    )

    assert spec["privileged"] is False
    assert spec["user"] == "1000:1000"
    assert spec["pids_limit"] == 200

    # Ensure termination of rogue targets fails
    with pytest.raises(SecurityViolationError):
        await manager.terminate_desktop("ancos-core-api")

    # Clean up
    await manager.terminate_desktop(spec["docker_id"])
    if os.path.exists(spec["workspace"]):
        shutil.rmtree(spec["workspace"], ignore_errors=True)


def test_audit_container_isolation_detects_violations():
    """Tests auditing helper detecting rogue container configurations."""
    broker = RestrictedDockerBroker()

    clean_info = {
        "privileged": False,
        "user": "1000:1000",
        "mounts": [{"Source": os.path.abspath("./data/workspaces/emp1"), "Destination": "/home/employee/workspace"}]
    }
    assert len(broker.audit_container_isolation(clean_info)) == 0

    rogue_info = {
        "privileged": True,
        "user": "root",
        "mounts": [
            {"Source": "/var/run/docker.sock", "Destination": "/var/run/docker.sock"},
            {"Source": "/", "Destination": "/host"}
        ]
    }
    violations = broker.audit_container_isolation(rogue_info)
    assert len(violations) >= 3
    assert any("docker.sock" in v.lower() for v in violations)
    assert any("privileged" in v.lower() for v in violations)
    assert any("root" in v.lower() for v in violations)

