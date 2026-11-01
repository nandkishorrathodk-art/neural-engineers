import os

import pytest
from neural.sandboxes.workstation import (
    ExecutionReceipt,
    SandboxConfig,
    SandboxManager,
    WorkstationSandbox,
)


@pytest.mark.asyncio
async def test_sandbox_python_execution_success():
    sandbox = WorkstationSandbox()
    async with sandbox:
        code = """
import sys
x = 21 * 2
print(f"Computed: {x}")
"""
        receipt: ExecutionReceipt = await sandbox.execute_python_code(code)
        assert receipt.exit_code == 0
        assert "Computed: 42" in receipt.stdout
        assert receipt.timed_out is False
        assert receipt.passed_safety is True
        assert len(receipt.signature) == 64
        assert receipt.execution_time_seconds > 0.0


@pytest.mark.asyncio
async def test_sandbox_timeout_enforcement():
    # Set short timeout of 0.25 seconds
    config = SandboxConfig(timeout_seconds=0.25)
    sandbox = WorkstationSandbox(config=config)
    async with sandbox:
        infinite_loop_code = """
import time
while True:
    time.sleep(0.05)
"""
        receipt = await sandbox.execute_python_code(infinite_loop_code, timeout=0.25)
        assert receipt.timed_out is True
        assert receipt.exit_code == 124
        assert "timed out" in receipt.stderr.lower()


@pytest.mark.asyncio
async def test_sandbox_safety_boundary_blocks_dangerous_patterns():
    sandbox = WorkstationSandbox()
    async with sandbox:
        dangerous_code = """
import os
__import__('os').system('echo pwned')
"""
        receipt = await sandbox.execute_python_code(dangerous_code)
        assert receipt.exit_code == -1
        assert receipt.passed_safety is False
        assert len(receipt.safety_violations) > 0
        assert "restricted pattern" in receipt.stderr.lower()


@pytest.mark.asyncio
async def test_sandbox_safety_boundary_blocks_directory_traversal():
    sandbox = WorkstationSandbox()
    async with sandbox:
        traversal_code = "path = '../../etc/shadow'"
        receipt = await sandbox.execute_python_code(traversal_code)
        assert receipt.exit_code == -1
        assert receipt.passed_safety is False
        assert any("traversal" in v.lower() for v in receipt.safety_violations)


@pytest.mark.asyncio
async def test_sandbox_test_runner_execution_and_receipt():
    sandbox = WorkstationSandbox()
    async with sandbox:
        solution_code = """
def add(a, b):
    return a + b
"""
        test_code = """
from solution import add
assert add(2, 3) == 5
assert add(-1, 1) == 0
print("ALL_ASSERTIONS_PASSED")
"""
        receipt = await sandbox.execute_python_code(
            code=solution_code,
            test_code=test_code,
            filename="solution.py",
        )
        assert receipt.exit_code == 0
        assert "ALL_ASSERTIONS_PASSED" in receipt.stdout


@pytest.mark.asyncio
async def test_sandbox_manager_lifecycle():
    manager = SandboxManager()
    sbx1 = await manager.create_sandbox()
    sbx2 = await manager.create_sandbox()

    assert sbx1.sandbox_id != sbx2.sandbox_id
    assert sbx1.workdir != sbx2.workdir
    assert os.path.exists(sbx1.workdir)
    assert os.path.exists(sbx2.workdir)

    workdir1 = sbx1.workdir
    await manager.release_sandbox(sbx1.sandbox_id)
    assert not os.path.exists(workdir1)

    await manager.cleanup_all()
    assert len(manager._sandboxes) == 0


@pytest.mark.asyncio
async def test_sandbox_blocks_filename_directory_traversal():
    sandbox = WorkstationSandbox()
    async with sandbox:
        traversal_filename = "../../escaped_test_file.py"
        receipt = await sandbox.execute_python_code(
            code="print('evil')",
            filename=traversal_filename,
        )
        assert receipt.exit_code == -1
        assert receipt.passed_safety is False
        assert any("directory traversal" in v.lower() for v in receipt.safety_violations)

        escaped_path = os.path.join(sandbox.workdir, traversal_filename)
        assert not os.path.exists(escaped_path)


@pytest.mark.asyncio
async def test_sandbox_execute_command_empty():
    sandbox = WorkstationSandbox()
    async with sandbox:
        # Empty list
        r1 = await sandbox.execute_command([])
        assert r1.exit_code == 1
        assert r1.passed_safety is False
        assert "empty command" in r1.stderr.lower()

        # Whitespace string
        r2 = await sandbox.execute_command("   ")
        assert r2.exit_code == 1
        assert r2.passed_safety is False


@pytest.mark.asyncio
async def test_sandbox_blocks_os_system_and_subprocess_patterns():
    sandbox = WorkstationSandbox()
    async with sandbox:
        code1 = "import os\nos.system('whoami')"
        r1 = await sandbox.execute_python_code(code1)
        assert r1.passed_safety is False
        assert any("system call" in v.lower() for v in r1.safety_violations)

        code2 = "import subprocess\nsubprocess.run(['dir'])"
        r2 = await sandbox.execute_python_code(code2)
        assert r2.passed_safety is False
        assert any("system call" in v.lower() for v in r2.safety_violations)

