"""
ANCOS NEURO-3: Explicit Task Mode Contracts and Capability Policy Enforcement.
Defines explicit execution contracts across all 5 modes:
- terminal_only (terminal)
- gui_only (gui)
- hybrid
- authorized_tools
- research_reasoning
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from neural.constants import ExecutionMode
from neural.exceptions import (
    CapabilityPolicyViolationError,
    ModeContractViolationError,
    StrictGUIContractViolation,
    UnauthorizedToolError,
)
from neural.logger import get_logger

logger = get_logger("ancos.execution.contracts")


class TaskCapability(str, Enum):
    TERMINAL_EXEC = "terminal_exec"
    WORKSPACE_FILE_IO = "workspace_file_io"
    TEST_EXEC = "test_exec"
    GUI_PERCEPTION = "gui_perception"
    GUI_MOTOR = "gui_motor"
    GUI_VERIFICATION = "gui_verification"
    TOOL_INVOCATION = "tool_invocation"
    COGNITIVE_REASONING = "cognitive_reasoning"
    MEMORY_SEARCH = "memory_search"
    REPORT_SYNTHESIS = "report_synthesis"


ALLOWLISTED_TERMINAL_EXECUTABLES: frozenset[str] = frozenset({
    "python",
    "python3",
    "pytest",
    "npm",
    "npx",
    "node",
    "git",
    "uv",
})

PROHIBITED_GUI_SHORTCUT_PATTERNS: list[str] = [
    r"document\.querySelector",
    r"document\.getElementById",
    r"eval\(",
    r"playwright",
    r"puppeteer",
    r"cdp\.",
    r"cdp\.send",
    r"page\.evaluate",
    r"\.evaluate\(",
    r"at-spi",
    r"accessibility_tree",
    r"xdotool\s+key",
    r"curl\s+",
    r"wget\s+",
    r"__import__\('sqlite3'\)",
    r"__import__\('os'\)\.system",
]

DESTRUCTIVE_COMMAND_KEYWORDS: frozenset[str] = frozenset({
    "rm -rf /",
    ":(){ :|:& };:",
    "drop table",
    "drop database",
    "truncate table",
    "wipe_disk",
    "format drive",
    "deploy_production",
    "destroy_cluster",
})


@dataclass(frozen=True)
class TaskExecutionModeContract:
    mode: ExecutionMode
    name: str
    description: str
    allowed_capabilities: frozenset[TaskCapability]
    forbidden_capabilities: frozenset[TaskCapability]
    allowlisted_executables: frozenset[str] = field(default_factory=frozenset)
    requires_visual_loop: bool = False
    allows_state_mutation: bool = True
    process_timeout_seconds: float = 300.0
    broker_deadline_seconds: float = 310.0
    worker_deadline_seconds: float = 320.0

    @property
    def allow_terminal(self) -> bool:
        return TaskCapability.TERMINAL_EXEC in self.allowed_capabilities

    @property
    def allow_gui(self) -> bool:
        return any(
            c in self.allowed_capabilities
            for c in (
                TaskCapability.GUI_PERCEPTION,
                TaskCapability.GUI_MOTOR,
                TaskCapability.GUI_VERIFICATION,
            )
        )

    @property
    def allow_tools(self) -> bool:
        return TaskCapability.TOOL_INVOCATION in self.allowed_capabilities

    @property
    def allow_state_mutation(self) -> bool:
        return self.allows_state_mutation

    @property
    def allow_visual_loop(self) -> bool:
        return self.requires_visual_loop

    @property
    def allow_cdp_dom_shortcuts(self) -> bool:
        return False

    @property
    def allow_direct_cli_shortcuts(self) -> bool:
        return False

    @property
    def allow_destructive_ops(self) -> bool:
        return False

    def is_capability_allowed(self, capability: TaskCapability) -> bool:
        return capability in self.allowed_capabilities and capability not in self.forbidden_capabilities

    def validate_capability(self, capability: TaskCapability) -> None:
        if not self.is_capability_allowed(capability):
            raise CapabilityPolicyViolationError(
                f"Capability '{capability.value}' is forbidden under execution mode contract '{self.mode.value}'."
            )

    def validate_command(self, command: list[str]) -> None:
        if not self.is_capability_allowed(TaskCapability.TERMINAL_EXEC):
            raise CapabilityPolicyViolationError(
                f"Terminal command execution is strictly forbidden under execution mode '{self.mode.value}'."
            )
        if not command:
            raise ModeContractViolationError("Command must not be empty.")
        exe = command[0]
        # Normalize executable: strip directory path and Windows .exe extension
        exe_lower = exe.lower()
        exe_base = exe_lower.replace("\\", "/").split("/")[-1]
        if exe_base.endswith(".exe"):
            exe_base = exe_base[:-4]

        if self.allowlisted_executables:
            if exe_base not in self.allowlisted_executables and exe_lower not in self.allowlisted_executables:
                raise CapabilityPolicyViolationError(
                    f"Executable '{exe}' is not in allowlisted binaries {sorted(self.allowlisted_executables)} "
                    f"for mode '{self.mode.value}'."
                )
        # Scan for dangerous destructive signatures
        joined = " ".join(command).lower()
        for kw in DESTRUCTIVE_COMMAND_KEYWORDS:
            if kw in joined:
                raise CapabilityPolicyViolationError(
                    f"Destructive command pattern '{kw}' detected and blocked by policy engine."
                )


TERMINAL_ONLY_CONTRACT = TaskExecutionModeContract(
    mode=ExecutionMode.TERMINAL_ONLY,
    name="Terminal Only Execution Contract",
    description="Permits bounded terminal commands and workspace file deliverables; strictly forbids GUI interactions and direct DOM cheating.",
    allowed_capabilities=frozenset({
        TaskCapability.TERMINAL_EXEC,
        TaskCapability.WORKSPACE_FILE_IO,
        TaskCapability.TEST_EXEC,
    }),
    forbidden_capabilities=frozenset({
        TaskCapability.GUI_PERCEPTION,
        TaskCapability.GUI_MOTOR,
        TaskCapability.GUI_VERIFICATION,
        TaskCapability.TOOL_INVOCATION,
    }),
    allowlisted_executables=ALLOWLISTED_TERMINAL_EXECUTABLES,
    requires_visual_loop=False,
    allows_state_mutation=True,
    process_timeout_seconds=300.0,
    broker_deadline_seconds=310.0,
    worker_deadline_seconds=320.0,
)

GUI_ONLY_CONTRACT = TaskExecutionModeContract(
    mode=ExecutionMode.GUI_ONLY,
    name="GUI Only Execution Contract",
    description="Permits pixel-based visual perception and motor actions; strictly forbids direct DOM injection, CLI shortcuts, and AT-SPI bypasses.",
    allowed_capabilities=frozenset({
        TaskCapability.GUI_PERCEPTION,
        TaskCapability.GUI_MOTOR,
        TaskCapability.GUI_VERIFICATION,
    }),
    forbidden_capabilities=frozenset({
        TaskCapability.TERMINAL_EXEC,
        TaskCapability.TOOL_INVOCATION,
    }),
    allowlisted_executables=frozenset(),
    requires_visual_loop=True,
    allows_state_mutation=True,
    process_timeout_seconds=300.0,
    broker_deadline_seconds=310.0,
    worker_deadline_seconds=320.0,
)

HYBRID_CONTRACT = TaskExecutionModeContract(
    mode=ExecutionMode.HYBRID,
    name="Hybrid Terminal & GUI Contract",
    description="Permits coordinated terminal and visual motor workflows, enforcing sandbox invariants on both interfaces.",
    allowed_capabilities=frozenset({
        TaskCapability.TERMINAL_EXEC,
        TaskCapability.WORKSPACE_FILE_IO,
        TaskCapability.TEST_EXEC,
        TaskCapability.GUI_PERCEPTION,
        TaskCapability.GUI_MOTOR,
        TaskCapability.GUI_VERIFICATION,
    }),
    forbidden_capabilities=frozenset({
        TaskCapability.TOOL_INVOCATION,
    }),
    allowlisted_executables=ALLOWLISTED_TERMINAL_EXECUTABLES,
    requires_visual_loop=True,
    allows_state_mutation=True,
    process_timeout_seconds=300.0,
    broker_deadline_seconds=310.0,
    worker_deadline_seconds=320.0,
)

AUTHORIZED_TOOLS_CONTRACT = TaskExecutionModeContract(
    mode=ExecutionMode.AUTHORIZED_TOOLS,
    name="Authorized Tools & Integrations Contract",
    description="Permits invocation of security-vetted, authorized tool definitions from the ToolRegistry; fails closed on unauthorized tools.",
    allowed_capabilities=frozenset({
        TaskCapability.TOOL_INVOCATION,
        TaskCapability.WORKSPACE_FILE_IO,
        TaskCapability.COGNITIVE_REASONING,
    }),
    forbidden_capabilities=frozenset({
        TaskCapability.TERMINAL_EXEC,
        TaskCapability.GUI_PERCEPTION,
        TaskCapability.GUI_MOTOR,
    }),
    allowlisted_executables=frozenset(),
    requires_visual_loop=False,
    allows_state_mutation=True,
    process_timeout_seconds=120.0,
    broker_deadline_seconds=130.0,
    worker_deadline_seconds=140.0,
)

RESEARCH_REASONING_CONTRACT = TaskExecutionModeContract(
    mode=ExecutionMode.RESEARCH_REASONING,
    name="Research & Reasoning Contract",
    description="Permits cognitive reasoning, memory retrieval, and report synthesis; strictly forbids unapproved container mutations or destructive actions.",
    allowed_capabilities=frozenset({
        TaskCapability.COGNITIVE_REASONING,
        TaskCapability.MEMORY_SEARCH,
        TaskCapability.REPORT_SYNTHESIS,
    }),
    forbidden_capabilities=frozenset({
        TaskCapability.TERMINAL_EXEC,
        TaskCapability.GUI_MOTOR,
        TaskCapability.GUI_VERIFICATION,
        TaskCapability.TOOL_INVOCATION,
    }),
    allowlisted_executables=frozenset(),
    requires_visual_loop=False,
    allows_state_mutation=False,
    process_timeout_seconds=180.0,
    broker_deadline_seconds=190.0,
    worker_deadline_seconds=200.0,
)

CONTRACT_REGISTRY: dict[ExecutionMode, TaskExecutionModeContract] = {
    ExecutionMode.TERMINAL: TERMINAL_ONLY_CONTRACT,
    ExecutionMode.TERMINAL_ONLY: TERMINAL_ONLY_CONTRACT,
    ExecutionMode.GUI: GUI_ONLY_CONTRACT,
    ExecutionMode.GUI_ONLY: GUI_ONLY_CONTRACT,
    ExecutionMode.HYBRID: HYBRID_CONTRACT,
    ExecutionMode.AUTHORIZED_TOOLS: AUTHORIZED_TOOLS_CONTRACT,
    ExecutionMode.RESEARCH_REASONING: RESEARCH_REASONING_CONTRACT,
}


def get_mode_contract(mode: ExecutionMode | str) -> TaskExecutionModeContract:
    normalized = ExecutionMode.normalize(mode)
    if normalized in CONTRACT_REGISTRY:
        return CONTRACT_REGISTRY[normalized]
    raise ModeContractViolationError(f"No execution contract registered for mode: '{mode}'.")


class CapabilityPolicyEnforcer:
    """
    Capability Policy Enforcement Engine.
    Enforces mode boundaries and strictly rejects forbidden operations,
    shortcuts, unvetted tools, and direct visual cheating.
    """

    @classmethod
    def validate_action(
        cls,
        mode: ExecutionMode | str,
        action_type: str,
        action_data: dict[str, Any] | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> None:
        contract = get_mode_contract(mode)
        action_type_norm = action_type.strip().lower()
        data = action_data or {}
        context = task_context or {}

        # 1. Mode: Terminal Only
        if ExecutionMode.is_terminal(contract.mode):
            if action_type_norm in {
                "gui_action",
                "gui_click",
                "gui_type",
                "gui_screenshot",
                "vnc_stream",
                "mouse_move",
                "key_press",
            }:
                raise CapabilityPolicyViolationError(
                    f"Terminal-only task mode contract strictly rejects GUI action '{action_type}'."
                )
            if action_type_norm == "tool_invocation":
                raise CapabilityPolicyViolationError(
                    "Terminal-only tasks cannot execute arbitrary external tool integrations."
                )
            if action_type_norm == "terminal_command":
                cmd = data.get("command", [])
                if isinstance(cmd, list):
                    contract.validate_command(cmd)

        # 2. Mode: GUI Only
        elif ExecutionMode.is_gui(contract.mode):
            if action_type_norm in {
                "terminal_command",
                "exec_shell",
                "bash",
                "sh",
                "run_command",
            }:
                raise CapabilityPolicyViolationError(
                    "Strict GUI-Only contract forbids direct terminal/CLI execution shortcuts; "
                    "tasks must interact strictly through visual perception and motor actions."
                )
            # Scan context and payload for prohibited shortcuts
            cls.validate_gui_context(data)
            cls.validate_gui_context(context)

        # 3. Mode: Hybrid
        elif contract.mode == ExecutionMode.HYBRID:
            if action_type_norm == "terminal_command":
                cmd = data.get("command", [])
                if isinstance(cmd, list):
                    contract.validate_command(cmd)
            elif action_type_norm.startswith("gui_"):
                cls.validate_gui_context(data)
                cls.validate_gui_context(context)

        # 4. Mode: Authorized Tools
        elif contract.mode == ExecutionMode.AUTHORIZED_TOOLS:
            if action_type_norm in {
                "terminal_command",
                "exec_shell",
                "gui_action",
                "gui_click",
            }:
                raise CapabilityPolicyViolationError(
                    f"Action '{action_type}' is prohibited under authorized_tools mode. "
                    "Only registered and authorized tools can be executed."
                )
            if action_type_norm == "tool_invocation":
                tool_name = data.get("tool_name", "")
                authorized_tools = context.get("authorized_tools") or []
                tool_record = data.get("tool_record")
                cls.enforce_tool_authorization(
                    tool_name=tool_name,
                    authorized_tools=authorized_tools,
                    tool_record=tool_record,
                )

        # 5. Mode: Research Reasoning
        elif contract.mode == ExecutionMode.RESEARCH_REASONING:
            if action_type_norm in {
                "terminal_command",
                "exec_shell",
                "gui_action",
                "gui_click",
                "wipe_disk",
                "delete_file",
            }:
                raise CapabilityPolicyViolationError(
                    f"Research & reasoning tasks are restricted to cognitive analysis and report synthesis; "
                    f"state-mutating action '{action_type}' is prohibited."
                )

    @classmethod
    def validate_gui_context(cls, context: dict[str, Any] | str) -> None:
        raw_text = str(context).lower()
        for pat in PROHIBITED_GUI_SHORTCUT_PATTERNS:
            if re.search(pat, raw_text, re.IGNORECASE):
                logger.error("Strict GUI contract violation detected", pattern=pat)
                raise StrictGUIContractViolation(
                    f"Execution violates Strict GUI-Only Contract: prohibited shortcut pattern '{pat}' detected."
                )

    @classmethod
    def enforce_tool_authorization(
        cls,
        tool_name: str,
        authorized_tools: list[str] | set[str] | frozenset[str],
        tool_record: Any | None = None,
    ) -> None:
        clean_name = str(tool_name).strip()
        if not clean_name:
            raise UnauthorizedToolError("Tool invocation rejected: tool_name is empty.")

        authorized_set = {str(t).strip().lower() for t in authorized_tools}
        if clean_name.lower() not in authorized_set:
            logger.warning("Unauthorized tool invocation blocked", tool=clean_name, allowed=list(authorized_set))
            raise UnauthorizedToolError(
                f"Tool '{clean_name}' is not in authorized tool allowlist: {sorted(authorized_set)}."
            )

        if tool_record is not None:
            safety_status = getattr(tool_record, "safety_status", None)
            if safety_status in {"violated", "rejected"}:
                raise UnauthorizedToolError(
                    f"Tool '{clean_name}' is blocked due to safety violations: "
                    f"{getattr(tool_record, 'safety_violations', [])}."
                )

