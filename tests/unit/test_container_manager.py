import os
import shutil

import pytest
from neural.constants import ContainerStatus
from neural.containers.manager import ContainerManager


@pytest.mark.asyncio
async def test_container_manager_port_allocation():
    manager = ContainerManager()

    vnc1, novnc1 = manager.allocate_ports("c1")
    assert vnc1 == 5901
    assert novnc1 == 6081

    vnc2, novnc2 = manager.allocate_ports("c2")
    assert vnc2 == 5902
    assert novnc2 == 6082

    # Same identifier returns same ports
    vnc1_again, novnc1_again = manager.allocate_ports("c1")
    assert vnc1_again == vnc1
    assert novnc1_again == novnc1

    # Release c1 and verify next allocation reuses 5901
    released = manager.release_ports("c1")
    assert released == (5901, 6081)

    vnc3, novnc3 = manager.allocate_ports("c3")
    assert vnc3 == 5901
    assert novnc3 == 6081

    manager.release_ports("c2")
    manager.release_ports("c3")


@pytest.mark.asyncio
async def test_container_manager_provision_spec_and_isolation():
    manager = ContainerManager()
    emp_id = "ANC-EMP-UNIT-TEST"

    spec = await manager.provision_employee_desktop(
        employee_id=emp_id,
        role_image="employee-swe",
        cpu_limit=1.5,
        mem_limit="2g"
    )

    assert spec["docker_id"] == f"ancos-desktop-{emp_id.lower()}"
    assert spec["image"] == "ancos/employee-swe:latest"
    assert spec["vnc_port"] >= 5901
    assert spec["novnc_port"] >= 6081
    assert spec["user"] == "1000:1000"
    assert spec["privileged"] is False
    assert spec["pids_limit"] == 200
    assert spec["cpu_limit"] == 1.5
    assert spec["mem_limit"] == "2g"
    assert spec["status"] == ContainerStatus.RUNNING.value
    assert os.path.exists(spec["workspace"])

    # Verify status query
    status = await manager.get_desktop_status(spec["docker_id"])
    assert status is not None
    assert status["docker_id"] == spec["docker_id"]

    # Terminate and verify status change
    await manager.terminate_desktop(spec["docker_id"])
    updated_status = await manager.get_desktop_status(spec["docker_id"])
    assert updated_status["status"] == ContainerStatus.TERMINATED.value

    # Clean up test workspace
    if os.path.exists(spec["workspace"]):
        shutil.rmtree(spec["workspace"], ignore_errors=True)

