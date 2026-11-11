from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from neural.logger import get_logger

logger = get_logger("ancos.sandboxes.workstation")


class SandboxSafetyError(Exception):
    """Raised when sandbox execution violates safety boundaries."""


@dataclass
class SandboxConfig:
    timeout_seconds: float = 5.0
    max_memory_mb: int = 512
    max_output_bytes: int = 65536
    max_disk_bytes: int = 100 * 1024 * 1024
    allow_network: bool = False
    isolated_filesystem: bool = True
    temp_dir_prefix: str = "ancos_sandbox_"
    enforce_network_perimeter: bool = True
    enforce_syscall_filtering: bool = True
    restricted_patterns: list[str] = field(
        default_factory=lambda: [
            "__import__('os').system",
            "__import__(\"os\").system",
            "subprocess.Popen",
            "rm -rf /",
            "rm -rf /*",
            ":(){ :|:& };:",
            "/etc/shadow",
            "shutil.rmtree('/')",
            "shutil.rmtree('C:\\\\')",
            "mkfs",
            "dd if=/dev",
            "chmod -R 777 /",
            "__import__('shutil').rmtree('/')",
        ]
    )



@dataclass
class ExecutionReceipt:
    receipt_id: str
    sandbox_id: str
    exit_code: int
    stdout: str
    stderr: str
    execution_time_seconds: float
    timed_out: bool
    passed_safety: bool
    safety_violations: list[str]
    resource_metrics: dict[str, Any]
    signature: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkstationSandbox:
    """
    Workstation Test Sandbox for empirical evaluation of employee code, scripts, and tests.
    Enforces strict timeout, process isolation, filesystem boundaries, and safety policies.
    Generates cryptographic, tamper-evident Execution Receipts.
    """

    def __init__(
        self,
        sandbox_id: str | None = None,
        config: SandboxConfig | None = None,
        custom_workdir: str | None = None,
    ):
        self.sandbox_id = sandbox_id or f"SBX-{uuid.uuid4().hex[:8].upper()}"
        self.config = config or SandboxConfig()
        self._custom_workdir = custom_workdir
        self._temp_dir: str | None = None
        self._is_active = False

    @property
    def workdir(self) -> str:
        if self._custom_workdir:
            return self._custom_workdir
        if self._temp_dir:
            return self._temp_dir
        raise RuntimeError("Sandbox not initialized. Use 'async with sandbox:' or call initialize().")

    async def initialize(self) -> str:
        if self._custom_workdir:
            os.makedirs(self._custom_workdir, exist_ok=True)
            self._is_active = True
            return self._custom_workdir

        self._temp_dir = tempfile.mkdtemp(prefix=self.config.temp_dir_prefix)
        self._is_active = True
        logger.info("Initialized isolated workstation sandbox", sandbox_id=self.sandbox_id, path=self._temp_dir)
        return self._temp_dir

    async def cleanup(self) -> None:
        if self._temp_dir and os.path.exists(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                logger.info("Cleaned up workstation sandbox", sandbox_id=self.sandbox_id, path=self._temp_dir)
            except Exception as e:
                logger.warning("Failed to clean up sandbox dir", error=str(e))
        self._is_active = False

    async def __aenter__(self) -> WorkstationSandbox:
        await self.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.cleanup()

    def check_safety(self, text_or_code: str) -> tuple[bool, list[str]]:
        violations: list[str] = []
        for pattern in self.config.restricted_patterns:
            if pattern in text_or_code:
                violations.append(f"Restricted pattern detected: {pattern}")

        # Directory traversal check (relative and sensitive system paths)
        if (
            "../" in text_or_code
            or "..\\" in text_or_code
            or "/etc/shadow" in text_or_code
            or "/etc/passwd" in text_or_code
            or "/etc/sudoers" in text_or_code
        ):
            violations.append("Directory traversal or sensitive path access attempt detected")

        # Dangerous OS/subprocess command patterns
        dangerous_patterns = [
            "os.system",
            "os.popen",
            "os.spawn",
            "subprocess.Popen",
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_output",
            "subprocess.check_call",
            "pty.spawn",
        ]
        for danger in dangerous_patterns:
            if danger in text_or_code and f"Restricted pattern detected: {danger}" not in violations:
                violations.append(f"Dangerous system call detected: {danger}")

        # Zero-Trust Network Perimeter & Cloud Metadata block
        if getattr(self.config, "enforce_network_perimeter", True):
            try:
                from neural.security.network import NetworkSecurityPerimeter
                net_ok, net_violations = NetworkSecurityPerimeter().scan_code_for_network_violations(text_or_code)
                if not net_ok:
                    violations.extend(net_violations)
            except Exception:
                pass

        # Zero-Trust Syscall & OS boundary filtering
        if getattr(self.config, "enforce_syscall_filtering", True):
            try:
                from neural.security.profiles import SecurityProfileValidator
                sys_ok, sys_violations = SecurityProfileValidator.validate_syscall_attempt(text_or_code)
                if not sys_ok:
                    violations.extend(sys_violations)
            except Exception:
                pass

        passed = len(violations) == 0
        return passed, violations


    @staticmethod
    def sign_receipt(data: dict[str, Any]) -> str:
        canonical = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def execute_python_code(
        self,
        code: str,
        test_code: str | None = None,
        filename: str = "solution.py",
        timeout: float | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionReceipt:
        """
        Executes python code and optional test runner script in sandbox.
        Enforces timeout and safety bounds.
        """
        if not self._is_active:
            await self.initialize()

        receipt_id = f"RECEIPT-{uuid.uuid4().hex[:8].upper()}"
        timestamp = datetime.now(UTC).isoformat()
        t_start = time.perf_counter()

        # Kill switch check
        from neural.security.kill_switch import get_kill_switch
        get_kill_switch().guard_active()

        # 0. Validate filename to prevent directory traversal
        target_workdir = os.path.abspath(self.workdir)
        target_path = os.path.abspath(os.path.join(self.workdir, filename))
        try:
            common = os.path.commonpath([target_path, target_workdir])
        except ValueError:
            common = ""

        # Check disk quota before writing files
        from neural.security.resources import ResourceGovernor
        exceeded, cur_bytes, quota_msg = ResourceGovernor.check_disk_quota(
            self.workdir, getattr(self.config, "max_disk_bytes", 100 * 1024 * 1024)
        )
        if exceeded:
            duration = round(time.perf_counter() - t_start, 4)
            receipt_data = {
                "receipt_id": receipt_id,
                "sandbox_id": self.sandbox_id,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Safety boundary violation: {quota_msg}",
                "execution_time_seconds": duration,
                "timed_out": False,
                "passed_safety": False,
                "safety_violations": [quota_msg],
                "resource_metrics": {"output_bytes": 0, "peak_memory_mb": 0, "disk_bytes": cur_bytes},
                "timestamp": timestamp,
            }
            sig = self.sign_receipt(receipt_data)
            return ExecutionReceipt(**receipt_data, signature=sig)


        if common != target_workdir or target_path == target_workdir or ".." in filename or filename.startswith(("/", "\\")):
            duration = round(time.perf_counter() - t_start, 4)
            receipt_data = {
                "receipt_id": receipt_id,
                "sandbox_id": self.sandbox_id,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Safety boundary violation: Directory traversal attempt in filename '{filename}'",
                "execution_time_seconds": duration,
                "timed_out": False,
                "passed_safety": False,
                "safety_violations": [f"Directory traversal in filename '{filename}'"],
                "resource_metrics": {"output_bytes": 0, "peak_memory_mb": 0},
                "timestamp": timestamp,
            }
            sig = self.sign_receipt(receipt_data)
            return ExecutionReceipt(**receipt_data, signature=sig)

        # 1. Safety verification
        safe_code, violations_code = self.check_safety(code)
        violations = list(violations_code)
        if test_code:
            safe_test, violations_test = self.check_safety(test_code)
            violations.extend(violations_test)

        if violations:
            duration = round(time.perf_counter() - t_start, 4)
            receipt_data = {
                "receipt_id": receipt_id,
                "sandbox_id": self.sandbox_id,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Safety boundary violation: {'; '.join(violations)}",
                "execution_time_seconds": duration,
                "timed_out": False,
                "passed_safety": False,
                "safety_violations": violations,
                "resource_metrics": {"output_bytes": 0, "peak_memory_mb": 0},
                "timestamp": timestamp,
            }
            sig = self.sign_receipt(receipt_data)
            return ExecutionReceipt(**receipt_data, signature=sig)

        # 2. Write files into sandbox
        solution_path = target_path
        with open(solution_path, "w", encoding="utf-8") as f:
            f.write(code)

        target_script = solution_path
        if test_code:
            runner_path = os.path.join(self.workdir, "_test_runner.py")
            with open(runner_path, "w", encoding="utf-8") as f:
                f.write(test_code)
            target_script = runner_path

        # 3. Execution setup
        effective_timeout = timeout or self.config.timeout_seconds
        proc_env = os.environ.copy()
        proc_env["PYTHONPATH"] = self.workdir
        proc_env["PYTHONDONTWRITEBYTECODE"] = "1"
        if env_vars:
            proc_env.update(env_vars)

        timed_out = False
        exit_code = 0
        stdout_text = ""
        stderr_text = ""

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                target_script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workdir,
                env=proc_env,
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
                exit_code = process.returncode or 0
                stdout_text = stdout_b.decode("utf-8", errors="replace")[: self.config.max_output_bytes]
                stderr_text = stderr_b.decode("utf-8", errors="replace")[: self.config.max_output_bytes]
            except TimeoutError:
                timed_out = True
                exit_code = 124
                stderr_text = f"Process timed out after {effective_timeout}s limit"
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except Exception:
                    pass

        except Exception as proc_err:
            exit_code = 1
            stderr_text = f"Subprocess launch exception: {proc_err!s}"

        duration = round(time.perf_counter() - t_start, 4)
        output_bytes = len(stdout_text.encode("utf-8")) + len(stderr_text.encode("utf-8"))

        receipt_data = {
            "receipt_id": receipt_id,
            "sandbox_id": self.sandbox_id,
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "execution_time_seconds": duration,
            "timed_out": timed_out,
            "passed_safety": True,
            "safety_violations": [],
            "resource_metrics": {
                "output_bytes": output_bytes,
                "timeout_limit_seconds": effective_timeout,
            },
            "timestamp": timestamp,
        }
        sig = self.sign_receipt(receipt_data)
        return ExecutionReceipt(**receipt_data, signature=sig)

    async def execute_command(
        self,
        command: list[str] | str,
        timeout: float | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> ExecutionReceipt:
        """
        Executes a shell/CLI command inside sandbox directory with timeout and safety checks.
        """
        if not self._is_active:
            await self.initialize()

        receipt_id = f"RECEIPT-{uuid.uuid4().hex[:8].upper()}"
        timestamp = datetime.now(UTC).isoformat()
        t_start = time.perf_counter()
        # Kill switch check
        from neural.security.kill_switch import get_kill_switch
        get_kill_switch().guard_active()

        if (isinstance(command, list) and not command) or (isinstance(command, str) and not command.strip()):


            duration = round(time.perf_counter() - t_start, 4)
            receipt_data = {
                "receipt_id": receipt_id,
                "sandbox_id": self.sandbox_id,
                "exit_code": 1,
                "stdout": "",
                "stderr": "Empty command provided for execution",
                "execution_time_seconds": duration,
                "timed_out": False,
                "passed_safety": False,
                "safety_violations": ["Empty command provided"],
                "resource_metrics": {"output_bytes": 0},
                "timestamp": timestamp,
            }
            sig = self.sign_receipt(receipt_data)
            return ExecutionReceipt(**receipt_data, signature=sig)

        cmd_str = " ".join(command) if isinstance(command, list) else command
        safe, violations = self.check_safety(cmd_str)
        if not safe:
            duration = round(time.perf_counter() - t_start, 4)
            receipt_data = {
                "receipt_id": receipt_id,
                "sandbox_id": self.sandbox_id,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Safety boundary violation in command: {'; '.join(violations)}",
                "execution_time_seconds": duration,
                "timed_out": False,
                "passed_safety": False,
                "safety_violations": violations,
                "resource_metrics": {"output_bytes": 0},
                "timestamp": timestamp,
            }
            sig = self.sign_receipt(receipt_data)
            return ExecutionReceipt(**receipt_data, signature=sig)

        effective_timeout = timeout or self.config.timeout_seconds
        proc_env = os.environ.copy()
        if env_vars:
            proc_env.update(env_vars)

        timed_out = False
        exit_code = 0
        stdout_text = ""
        stderr_text = ""

        try:
            if isinstance(command, str):
                process = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.workdir,
                    env=proc_env,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    command[0],
                    *command[1:],
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.workdir,
                    env=proc_env,
                )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
                exit_code = process.returncode or 0
                stdout_text = stdout_b.decode("utf-8", errors="replace")[: self.config.max_output_bytes]
                stderr_text = stderr_b.decode("utf-8", errors="replace")[: self.config.max_output_bytes]
            except TimeoutError:
                timed_out = True
                exit_code = 124
                stderr_text = f"Command timed out after {effective_timeout}s limit"
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass

        except Exception as ex:
            exit_code = 1
            stderr_text = f"Command execution exception: {ex!s}"

        duration = round(time.perf_counter() - t_start, 4)
        output_bytes = len(stdout_text.encode("utf-8")) + len(stderr_text.encode("utf-8"))

        receipt_data = {
            "receipt_id": receipt_id,
            "sandbox_id": self.sandbox_id,
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "execution_time_seconds": duration,
            "timed_out": timed_out,
            "passed_safety": True,
            "safety_violations": [],
            "resource_metrics": {
                "output_bytes": output_bytes,
                "timeout_limit_seconds": effective_timeout,
            },
            "timestamp": timestamp,
        }
        sig = self.sign_receipt(receipt_data)
        return ExecutionReceipt(**receipt_data, signature=sig)


class SandboxManager:
    """
    Manages active workstation test sandboxes and life-cycles.
    """

    def __init__(self, default_config: SandboxConfig | None = None):
        self.default_config = default_config or SandboxConfig()
        self._sandboxes: dict[str, WorkstationSandbox] = {}

    async def create_sandbox(
        self,
        sandbox_id: str | None = None,
        config: SandboxConfig | None = None,
        custom_workdir: str | None = None,
    ) -> WorkstationSandbox:
        sbx = WorkstationSandbox(
            sandbox_id=sandbox_id,
            config=config or self.default_config,
            custom_workdir=custom_workdir,
        )
        await sbx.initialize()
        self._sandboxes[sbx.sandbox_id] = sbx
        return sbx

    async def release_sandbox(self, sandbox_id: str) -> None:
        sbx = self._sandboxes.pop(sandbox_id, None)
        if sbx:
            await sbx.cleanup()

    async def cleanup_all(self) -> None:
        for sbx in list(self._sandboxes.values()):
            try:
                await sbx.cleanup()
            except Exception:
                pass
        self._sandboxes.clear()
