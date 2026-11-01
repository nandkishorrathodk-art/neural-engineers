from __future__ import annotations

import os
import re
from typing import Any

from neural.exceptions import DiskQuotaExceededError
from neural.logger import get_logger

logger = get_logger("ancos.security.resources")


class ResourceGovernor:
    """
    Enforces strict execution resource limits, cgroups configurations,
    execution timeouts, and filesystem disk quotas across workspaces and sandboxes.
    """

    @staticmethod
    def parse_memory_to_bytes(mem_str: str | int) -> int:
        """Parses human-readable memory strings (e.g. '512m', '4g', '4Gi', '1024k') to bytes."""
        if isinstance(mem_str, int):
            return mem_str
        s = str(mem_str).strip().lower()
        m = re.match(r"^(\d+(?:\.\d+)?)\s*([a-z]+)?$", s)
        if not m:
            return 512 * 1024 * 1024  # Default 512MB fallback

        val_str, unit_raw = m.groups()
        num = float(val_str)
        unit = unit_raw or ""

        if unit in ("tib", "ti", "tb", "t"):
            return int(num * 1024 * 1024 * 1024 * 1024)
        if unit in ("gib", "gi", "gb", "g"):
            return int(num * 1024 * 1024 * 1024)
        if unit in ("mib", "mi", "mb", "m"):
            return int(num * 1024 * 1024)
        if unit in ("kib", "ki", "kb", "k"):
            return int(num * 1024)
        if unit in ("b", ""):
            return int(num)

        return 512 * 1024 * 1024

    @staticmethod
    def calculate_dir_size(path: str) -> int:
        """Calculates total byte size of a directory recursively."""
        if not os.path.exists(path):
            return 0
        if os.path.isfile(path):
            return os.path.getsize(path)

        total_bytes = 0
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total_bytes += os.path.getsize(fp)
                except (OSError, FileNotFoundError):
                    pass
        return total_bytes

    @classmethod
    def check_disk_quota(
        cls,
        directory_path: str,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> tuple[bool, int, str]:
        """
        Checks if directory usage exceeds the allocated disk quota.
        Returns (exceeded: bool, current_bytes: int, message: str).
        """
        current_bytes = cls.calculate_dir_size(directory_path)
        exceeded = current_bytes > max_bytes
        if exceeded:
            msg = (
                f"Disk quota exceeded: directory '{directory_path}' consumes {current_bytes} bytes "
                f"(max allowed: {max_bytes} bytes)"
            )
        else:
            msg = f"Disk quota nominal: {current_bytes}/{max_bytes} bytes used"
        return exceeded, current_bytes, msg

    @classmethod
    def enforce_disk_write(
        cls,
        directory_path: str,
        incoming_bytes: int,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        """
        Validates whether adding incoming_bytes to directory_path exceeds quota.
        Raises DiskQuotaExceededError if limit would be breached.
        """
        current_bytes = cls.calculate_dir_size(directory_path)
        projected = current_bytes + incoming_bytes
        if projected > max_bytes:
            msg = (
                f"Disk write rejected: writing {incoming_bytes} bytes would exceed quota "
                f"({projected} bytes projected > {max_bytes} bytes max)"
            )
            logger.warning("Disk quota write blocked", directory=directory_path, projected=projected)
            raise DiskQuotaExceededError(msg)

    @classmethod
    def get_cgroup_spec(
        cls,
        cpu_limit: float = 2.0,
        mem_limit: str | int = "4g",
        pids_limit: int = 200,
        disk_quota_mb: int = 250,
    ) -> dict[str, Any]:
        """
        Generates formal cgroups and resource limits configuration for containers and sandboxes.
        """
        nano_cpus = int(cpu_limit * 1e9)
        mem_bytes = cls.parse_memory_to_bytes(mem_limit)
        disk_bytes = disk_quota_mb * 1024 * 1024

        return {
            "nano_cpus": nano_cpus,
            "cpu_limit": cpu_limit,
            "mem_limit_bytes": mem_bytes,
            "mem_limit_str": str(mem_limit),
            "pids_limit": pids_limit,
            "disk_quota_bytes": disk_bytes,
            "disk_quota_mb": disk_quota_mb,
            "cgroup_v2": {
                "cpu.max": f"{int(cpu_limit * 100000)} 100000",
                "memory.max": str(mem_bytes),
                "pids.max": str(pids_limit),
            },
        }

