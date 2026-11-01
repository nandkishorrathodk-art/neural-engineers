from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from neural.db.models import ToolRecord
from neural.db.repositories import ToolRepository
from neural.logger import get_logger
from neural.sandboxes.workstation import ExecutionReceipt, WorkstationSandbox
from neural.tools.registry import ToolRegistry
from neural.tools.safety import ToolASTSafetyValidator, ToolSafetyError, ToolSafetyReport

logger = get_logger("ancos.tools.engine")


class ToolsmithEngine:
    """
    Autonomous Toolsmith Engine.
    Handles autonomous creation, compilation, AST safety validation,
    automated sandbox testing, dynamic registration, and secure execution of tools.
    """

    def __init__(
        self,
        tool_repo: ToolRepository | None = None,
        sandbox: WorkstationSandbox | None = None,
        registry: ToolRegistry | None = None,
        safety_validator: ToolASTSafetyValidator | None = None,
    ) -> None:
        self.tool_repo = tool_repo
        self.sandbox = sandbox or WorkstationSandbox()
        self.safety_validator = safety_validator or ToolASTSafetyValidator()
        self.registry = registry or ToolRegistry(tool_repo=tool_repo)

    async def author_tool(
        self,
        name: str,
        code: str,
        test_code: str,
        description: str = "",
        version: str = "1.0.0",
        category: str = "utility",
        interface_schema: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        author_id: str = "autonomous_toolsmith",
        auto_test: bool = True,
        timeout: float = 5.0,
    ) -> tuple[ToolRecord, ExecutionReceipt | None]:
        logger.info("Authoring specialized tool", name=name, version=version, author=author_id)
        dependencies = dependencies or []

        # 1. AST Safety Validation on code
        safety_report: ToolSafetyReport = self.safety_validator.validate(code)
        if not safety_report.passed:
            logger.warning("Tool failed AST safety validation", name=name, violations=safety_report.violations)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            tool_rec = ToolRecord(
                id=str(uuid.uuid4()),
                tool_id=f"TOOL-{name.upper().replace('_', '-')}-{uuid.uuid4().hex[:4].upper()}",
                name=name,
                version=version,
                description=description,
                category=category,
                code=code,
                test_code=test_code,
                interface_schema=interface_schema or {},
                dependencies=dependencies,
                author_id=author_id,
                status="rejected",
                safety_status="violated",
                safety_violations=safety_report.violations,
                created_at=now,
                updated_at=now,
            )
            if self.tool_repo:
                tool_rec = await self.tool_repo.create_tool(
                    name=name,
                    version=version,
                    description=description,
                    category=category,
                    code=code,
                    test_code=test_code,
                    interface_schema=interface_schema or {},
                    dependencies=dependencies,
                    author_id=author_id,
                    status="rejected",
                    safety_status="violated",
                    safety_violations=safety_report.violations,
                )
            return tool_rec, None

        # 2. Also check test code safety
        test_safety = self.safety_validator.validate(test_code)
        if not test_safety.passed:
            logger.warning("Tool test harness failed AST safety", name=name, violations=test_safety.violations)
            violations = [f"Test safety: {v}" for v in test_safety.violations]
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            tool_rec = ToolRecord(
                id=str(uuid.uuid4()),
                tool_id=f"TOOL-{name.upper().replace('_', '-')}-{uuid.uuid4().hex[:4].upper()}",
                name=name,
                version=version,
                description=description,
                category=category,
                code=code,
                test_code=test_code,
                interface_schema=interface_schema or {},
                dependencies=dependencies,
                author_id=author_id,
                status="rejected",
                safety_status="violated",
                safety_violations=violations,
                created_at=now,
                updated_at=now,
            )
            if self.tool_repo:
                tool_rec = await self.tool_repo.create_tool(
                    name=name,
                    version=version,
                    description=description,
                    category=category,
                    code=code,
                    test_code=test_code,
                    interface_schema=interface_schema or {},
                    dependencies=dependencies,
                    author_id=author_id,
                    status="rejected",
                    safety_status="violated",
                    safety_violations=violations,
                )
            return tool_rec, None

        # 3. Check dependency isolation
        deps_valid, dep_violations = self.registry.validate_dependencies(dependencies)
        if not deps_valid:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            tool_rec = ToolRecord(
                id=str(uuid.uuid4()),
                tool_id=f"TOOL-{name.upper().replace('_', '-')}-{uuid.uuid4().hex[:4].upper()}",
                name=name,
                version=version,
                description=description,
                category=category,
                code=code,
                test_code=test_code,
                interface_schema=interface_schema or {},
                dependencies=dependencies,
                author_id=author_id,
                status="rejected",
                safety_status="violated",
                safety_violations=dep_violations,
                created_at=now,
                updated_at=now,
            )
            if self.tool_repo:
                tool_rec = await self.tool_repo.create_tool(
                    name=name,
                    version=version,
                    description=description,
                    category=category,
                    code=code,
                    test_code=test_code,
                    interface_schema=interface_schema or {},
                    dependencies=dependencies,
                    author_id=author_id,
                    status="rejected",
                    safety_status="violated",
                    safety_violations=dep_violations,
                )
            return tool_rec, None

        # 4. Inferred interface if none provided
        effective_schema = interface_schema or safety_report.inferred_interface

        # 5. Persist initial tool record
        tool_status = "testing" if auto_test else "draft"
        tool_rec: ToolRecord
        if self.tool_repo:
            tool_rec = await self.tool_repo.create_tool(
                name=name,
                version=version,
                description=description,
                category=category,
                code=code,
                test_code=test_code,
                interface_schema=effective_schema,
                dependencies=dependencies,
                author_id=author_id,
                status=tool_status,
                safety_status="passed",
            )
        else:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            tool_rec = ToolRecord(
                id=str(uuid.uuid4()),
                tool_id=f"TOOL-{name.upper().replace('_', '-')}-{uuid.uuid4().hex[:4].upper()}",
                name=name,
                version=version,
                description=description,
                category=category,
                code=code,
                test_code=test_code,
                interface_schema=effective_schema,
                dependencies=dependencies,
                author_id=author_id,
                status=tool_status,
                safety_status="passed",
                created_at=now,
                updated_at=now,
            )

        receipt: ExecutionReceipt | None = None
        if auto_test:
            receipt = await self._run_sandbox_test(tool_rec, timeout=timeout)
            if receipt.exit_code == 0 and receipt.passed_safety and not receipt.timed_out:
                tool_rec.status = "verified"
                tool_rec.verification_receipt = receipt.to_dict()
                if self.tool_repo:
                    await self.tool_repo.update_status(
                        tool_rec.id,
                        status="verified",
                        verification_receipt=receipt.to_dict(),
                        safety_status="passed",
                    )
                await self.registry.register(tool_rec)
            else:
                tool_rec.status = "rejected"
                tool_rec.verification_receipt = receipt.to_dict()
                if self.tool_repo:
                    await self.tool_repo.update_status(
                        tool_rec.id,
                        status="rejected",
                        verification_receipt=receipt.to_dict(),
                        safety_status="violated" if not receipt.passed_safety else "passed",
                    )
        else:
            await self.registry.register(tool_rec)

        return tool_rec, receipt

    async def _run_sandbox_test(
        self,
        tool: ToolRecord,
        test_override: str | None = None,
        timeout: float = 5.0,
    ) -> ExecutionReceipt:
        test_code_to_run = test_override or tool.test_code
        async with self.sandbox as sbx:
            receipt = await sbx.execute_python_code(
                code=tool.code,
                test_code=test_code_to_run,
                filename="tool.py",
                timeout=timeout,
            )
            return receipt

    async def test_tool(
        self,
        tool_id_or_name: str,
        test_override: str | None = None,
        timeout: float = 5.0,
    ) -> ExecutionReceipt:
        tool: ToolRecord | None = None
        if self.tool_repo:
            tool = await self.tool_repo.get_by_tool_id(tool_id_or_name)
            if not tool:
                tool = await self.tool_repo.get_by_id(tool_id_or_name)

        if not tool:
            tool = await self.registry.get_tool(tool_id_or_name)

        if not tool:
            raise ValueError(f"Tool '{tool_id_or_name}' not found")

        receipt = await self._run_sandbox_test(tool, test_override=test_override, timeout=timeout)
        if receipt.exit_code == 0 and receipt.passed_safety and not receipt.timed_out:
            new_status = "verified"
        else:
            new_status = "rejected"

        if self.tool_repo and tool.id:
            await self.tool_repo.update_status(
                tool.id,
                status=new_status,
                verification_receipt=receipt.to_dict(),
            )
        tool.status = new_status
        tool.verification_receipt = receipt.to_dict()
        return receipt

    async def execute_tool(
        self,
        tool_name_or_id: str,
        function_name: str,
        arguments: dict[str, Any],
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """
        Securely executes a verified tool function inside the workstation sandbox.
        """
        tool = await self.registry.get_tool(tool_name_or_id, verified_only=True)
        if not tool and self.tool_repo:
            tool = await self.tool_repo.get_by_tool_id(tool_name_or_id)
            if not tool:
                tool = await self.tool_repo.get_by_id(tool_name_or_id)

        if not tool:
            raise ValueError(f"Tool '{tool_name_or_id}' not found or not verified")

        if tool.status != "verified":
            raise ToolSafetyError(f"Tool '{tool.name}' is not verified (status={tool.status})")

        # Harness to invoke function with args and output JSON
        args_json = json.dumps(arguments)
        marker = f"__ANCOS_RESULT_{uuid.uuid4().hex}__"
        runner_script = f"""
import asyncio
import inspect
import json
import tool

args = json.loads({repr(args_json)})
func = getattr(tool, {repr(function_name)}, None)
if not func:
    raise AttributeError(f"Function {repr(function_name)} not found in tool")

if inspect.iscoroutinefunction(func):
    res = asyncio.run(func(**args))
else:
    res = func(**args)

print({repr(marker)})
print(json.dumps(res, default=str))
"""
        async with self.sandbox as sbx:
            receipt = await sbx.execute_python_code(
                code=tool.code,
                test_code=runner_script,
                filename="tool.py",
                timeout=timeout,
            )

        if receipt.exit_code != 0:
            return {
                "status": "error",
                "error": receipt.stderr or "Tool execution failed",
                "receipt": receipt.to_dict(),
            }

        stdout = receipt.stdout
        if marker not in stdout:
            return {
                "status": "error",
                "error": "No output marker received from tool execution",
                "receipt": receipt.to_dict(),
            }

        result_part = stdout.rsplit(marker, 1)[-1].strip()
        try:
            parsed_result = json.loads(result_part)
        except json.JSONDecodeError:
            parsed_result = result_part

        return {
            "status": "success",
            "result": parsed_result,
            "receipt": receipt.to_dict(),
        }

