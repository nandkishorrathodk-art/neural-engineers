"""
ANCOS NEURO-3 Unified Execution Fabric.
"""

from neural.execution.contracts import (
    ALLOWLISTED_TERMINAL_EXECUTABLES,
    AUTHORIZED_TOOLS_CONTRACT,
    CONTRACT_REGISTRY,
    DESTRUCTIVE_COMMAND_KEYWORDS,
    GUI_ONLY_CONTRACT,
    HYBRID_CONTRACT,
    PROHIBITED_GUI_SHORTCUT_PATTERNS,
    RESEARCH_REASONING_CONTRACT,
    TERMINAL_ONLY_CONTRACT,
    CapabilityPolicyEnforcer,
    TaskCapability,
    TaskExecutionModeContract,
    get_mode_contract,
)
from neural.execution.fabric import UnifiedExecutionFabric

__all__ = [
    "ALLOWLISTED_TERMINAL_EXECUTABLES",
    "AUTHORIZED_TOOLS_CONTRACT",
    "CONTRACT_REGISTRY",
    "DESTRUCTIVE_COMMAND_KEYWORDS",
    "GUI_ONLY_CONTRACT",
    "HYBRID_CONTRACT",
    "PROHIBITED_GUI_SHORTCUT_PATTERNS",
    "RESEARCH_REASONING_CONTRACT",
    "TERMINAL_ONLY_CONTRACT",
    "CapabilityPolicyEnforcer",
    "TaskCapability",
    "TaskExecutionModeContract",
    "UnifiedExecutionFabric",
    "get_mode_contract",
]

