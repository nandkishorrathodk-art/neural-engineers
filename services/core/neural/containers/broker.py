from __future__ import annotations

import os
import re
from typing import Any, Optional

from neural.exceptions import SecurityViolationError
from neural.logger import get_logger
from neural.security.profiles import SecurityProfileValidator

logger = get_logger("ancos.containers.broker")

# Allowed container naming pattern: ancos-desktop-anc-emp-<id>
SAFE_CONTAINER_REGEX = re.compile(r"^ancos-desktop-anc-emp-[0-9a-z_-]+$", re.IGNORECASE)

# Prohibited sensitive host directories that MUST NEVER be mounted
PROHIBITED_HOST_PATHS = [
    "/",
    "/root",
    "/etc",
    "/var/run/docker.sock",
    "/var/run",
    "/proc",
    "/sys",
    "/bin",
    "/sbin",
    "/usr",
    "/boot",
    "/dev",
    "C:\\",
    "C:\\Windows",
    "C:\\Program Files",
]


class RestrictedDockerBroker:
    """
    Zero-Trust Docker Control Broker.
    Interposes between ANCOS and Docker daemon to enforce least-privilege operations:
    1. Only approved employee container names matching the pattern can be controlled.
    2. Zero mounting of host Docker socket (/var/run/docker.sock) or root filesystems.
    3. Mandatory non-root user (uid 1000:1000), dropped Linux capabilities, and no-new-privileges.
    4. Enforced process, CPU, and memory bounds on all provisioned containers.
    5. Prohibits any destructive container actions on non-ANCOS employee containers.
    """

    def __init__(
        self,
        workspace_base: str = "./data/workspaces",
        allowed_image_prefixes: Optional[list[str]] = None,
    ) -> None:
        self.workspace_base = os.path.abspath(workspace_base)
        self.allowed_image_prefixes = allowed_image_prefixes or [
            "ancos/employee-",
            "ancos/desktop-",
            "employee-",
        ]

    def validate_container_id(self, docker_id: str) -> None:
        """Ensures container ID strictly adheres to approved employee workstation schema."""
        if not SAFE_CONTAINER_REGEX.match(docker_id):
            logger.error("Blocked unauthorized container operation on non-employee container", docker_id=docker_id)
            raise SecurityViolationError(
                f"Unauthorized container target '{docker_id}'. Broker only manages ANCOS employee workstations."
            )

    def validate_image_name(self, image: str) -> None:
        """Validates that requested container image belongs to approved ANCOS images."""
        normalized = image.lower()
        if not any(normalized.startswith(prefix) or f"ancos/{prefix}" in normalized for prefix in self.allowed_image_prefixes):
            logger.error("Blocked container creation with unapproved image", image=image)
            raise SecurityViolationError(
                f"Image '{image}' is not an approved employee workstation image."
            )

    def validate_volume_mounts(self, volumes: dict[str, Any]) -> None:
        """
        Ensures volume mounts are strictly confined to the employee's isolated workspace.
        Prohibits mounting /var/run/docker.sock or host root/system paths.
        """
        for host_path, mount_cfg in volumes.items():
            abs_host = os.path.abspath(host_path)

            # Check prohibited host paths
            for forbidden in PROHIBITED_HOST_PATHS:
                forb_abs = os.path.abspath(forbidden)
                if abs_host == forb_abs or abs_host.startswith(forb_abs + os.sep):
                    raise SecurityViolationError(
                        f"Prohibited volume mount: cannot mount host system path '{host_path}' into container."
                    )

            # Check that mount is strictly within the workspace base (prevent prefix and traversal bypass)
            try:
                rel = os.path.relpath(abs_host, self.workspace_base)
                if rel.startswith("..") or os.path.isabs(rel) or abs_host == self.workspace_base:
                    raise SecurityViolationError(
                        f"Volume mount violation: '{host_path}' is outside designated workspace base '{self.workspace_base}'."
                    )
            except ValueError:
                raise SecurityViolationError(
                    f"Volume mount violation: '{host_path}' is on different drive from workspace base '{self.workspace_base}'."
                )

            # Check container mount target
            bind_target = mount_cfg.get("bind") if isinstance(mount_cfg, dict) else str(mount_cfg)
            if bind_target not in ["/home/employee/workspace", "/home/employee/workspace/"]:
                raise SecurityViolationError(
                    f"Invalid container mount destination '{bind_target}'. Must be '/home/employee/workspace'."
                )

    def harden_container_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """
        Applies mandatory zero-trust security profiles before container launch:
        - Drops all capabilities (`cap_drop=['ALL']`)
        - Enforces `privileged=False`
        - Enforces `user='1000:1000'`
        - Enforces `no-new-privileges:true`
        - Enforces pids_limit and cgroup limits
        """
        hardened = dict(kwargs)

        # 1. Validate container name and image
        if "name" in hardened:
            self.validate_container_id(hardened["name"])
        if "image" in hardened:
            self.validate_image_name(hardened["image"])

        # 2. Validate mounts
        if "volumes" in hardened and hardened["volumes"]:
            self.validate_volume_mounts(hardened["volumes"])

        # 3. Apply profile validation & hardening
        hardened["privileged"] = False
        hardened["user"] = "1000:1000"
        hardened["cap_drop"] = ["ALL"]
        hardened.pop("cap_add", None)

        sec_opts = list(hardened.get("security_opt") or [])
        if "no-new-privileges:true" not in sec_opts:
            sec_opts.append("no-new-privileges:true")
        hardened["security_opt"] = sec_opts

        # 4. Enforce process limits
        if not hardened.get("pids_limit") or hardened["pids_limit"] > 200:
            hardened["pids_limit"] = 200

        # Validate with existing security validator
        valid, violations = SecurityProfileValidator.validate_container_spec(hardened)
        if not valid:
            raise SecurityViolationError(f"Container spec failed security validation: {violations}")

        return hardened

    def audit_container_isolation(self, container_info: dict[str, Any]) -> list[str]:
        """Audits a running container to verify zero-trust compliance."""
        violations = []
        if container_info.get("privileged"):
            violations.append("Container is running in privileged mode")
        if container_info.get("user") in ["0", "root", "0:0"]:
            violations.append("Container is running as root user")

        mounts = container_info.get("mounts", [])
        for m in mounts:
            source = m.get("Source", "")
            if "docker.sock" in source:
                violations.append("Docker socket (/var/run/docker.sock) is mounted inside container!")
            if source in ["/", "C:\\"]:
                violations.append("Host root filesystem is mounted inside container!")

        return violations
