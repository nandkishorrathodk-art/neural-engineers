from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from neural.logger import get_logger

logger = get_logger("ancos.resource_scheduler.profiler")

# Current deployment operates one GUI workstation at a time. Hardware profiles
# remain useful for per-container resource sizing, but do not increase this cap.
MAX_ACTIVE_DESKTOPS = 1


@dataclass
class HardwareProfile:
    profile_name: str
    total_ram_gb: float
    max_active_desktops: int
    safe_ram_headroom_gb: float
    cpu_core_count: int
    max_cpu_percent_threshold: float = 85.0
    container_mem_limit: str = "2g"
    container_cpu_limit: float = 1.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "total_ram_gb": self.total_ram_gb,
            "max_active_desktops": self.max_active_desktops,
            "safe_ram_headroom_gb": self.safe_ram_headroom_gb,
            "cpu_core_count": self.cpu_core_count,
            "max_cpu_percent_threshold": self.max_cpu_percent_threshold,
            "container_mem_limit": self.container_mem_limit,
            "container_cpu_limit": self.container_cpu_limit,
        }


# Standard Hardware Profiles:
# 16 GB baseline machine enforces strictly 4 active GUI desktop sessions
PROFILE_16GB = HardwareProfile(
    profile_name="16GB_PROFILE",
    total_ram_gb=16.0,
    max_active_desktops=4,
    safe_ram_headroom_gb=1.5,
    cpu_core_count=8,
    max_cpu_percent_threshold=85.0,
    container_mem_limit="2g",
    container_cpu_limit="1.5",
)

PROFILE_32GB = HardwareProfile(
    profile_name="32GB_PROFILE",
    total_ram_gb=32.0,
    max_active_desktops=8,
    safe_ram_headroom_gb=3.0,
    cpu_core_count=16,
    max_cpu_percent_threshold=88.0,
    container_mem_limit="2.5g",
    container_cpu_limit="2.0",
)

PROFILE_64GB = HardwareProfile(
    profile_name="64GB_PROFILE",
    total_ram_gb=64.0,
    max_active_desktops=16,
    safe_ram_headroom_gb=6.0,
    cpu_core_count=32,
    max_cpu_percent_threshold=90.0,
    container_mem_limit="3g",
    container_cpu_limit="2.5",
)


class HardwareProfiler:
    """
    Hardware Profiler & Dynamic Capacity Manager.
    Detects physical host hardware, configures capacity profiles,
    and supports seamless scaling from 16 GB to 64 GB without organizational rewrites.
    """

    def __init__(self, profile: Optional[HardwareProfile] = None) -> None:
        self.active_profile = profile or PROFILE_16GB
        logger.info(
            "Initialized HardwareProfiler",
            profile=self.active_profile.profile_name,
            max_desktops=self.active_profile.max_active_desktops,
            total_ram_gb=self.active_profile.total_ram_gb,
        )

    @classmethod
    def detect_host_profile(cls) -> HardwareProfile:
        total_ram_gb = 16.0
        cpu_cores = os.cpu_count() or 4

        try:
            import psutil
            mem = psutil.virtual_memory()
            total_ram_gb = round(mem.total / (1024 ** 3), 1)
        except Exception:
            pass

        if total_ram_gb >= 56.0:
            base = PROFILE_64GB
        elif total_ram_gb >= 28.0:
            base = PROFILE_32GB
        else:
            base = PROFILE_16GB

        return HardwareProfile(
            profile_name=base.profile_name,
            total_ram_gb=total_ram_gb,
            max_active_desktops=base.max_active_desktops,
            safe_ram_headroom_gb=base.safe_ram_headroom_gb,
            cpu_core_count=cpu_cores,
            max_cpu_percent_threshold=base.max_cpu_percent_threshold,
            container_mem_limit=base.container_mem_limit,
            container_cpu_limit=base.container_cpu_limit,
        )

    def set_profile(self, profile: HardwareProfile | str) -> None:
        if isinstance(profile, str):
            p_upper = profile.upper()
            if "64" in p_upper:
                self.active_profile = PROFILE_64GB
            elif "32" in p_upper:
                self.active_profile = PROFILE_32GB
            else:
                self.active_profile = PROFILE_16GB
        else:
            self.active_profile = profile

        logger.info(
            "Hardware capacity profile updated",
            profile=self.active_profile.profile_name,
            max_desktops=self.active_profile.max_active_desktops,
        )

    def get_max_desktops(self) -> int:
        return min(self.active_profile.max_active_desktops, MAX_ACTIVE_DESKTOPS)

    def get_headroom_gb(self) -> float:
        return self.active_profile.safe_ram_headroom_gb

