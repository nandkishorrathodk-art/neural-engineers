from __future__ import annotations

import time
from typing import Optional

from neural.logger import get_logger
from neural.resource_scheduler.hardware_profiler import HardwareProfile

logger = get_logger("ancos.resource_scheduler.monitor")


class ResourceMonitor:
    """
    Live Host CPU & RAM Telemetry Monitor.
    Enforces dynamic admission thresholds and detects emergency host resource exhaustion.
    """

    def __init__(self) -> None:
        self._mock_metrics: Optional[dict[str, float]] = None

    def set_mock_metrics(
        self,
        free_ram_gb: Optional[float] = None,
        cpu_percent: Optional[float] = None,
        used_ram_percent: Optional[float] = None,
    ) -> None:
        """Used in test suites to verify admission control and preemption under pressure."""
        self._mock_metrics = {
            "free_ram_gb": free_ram_gb if free_ram_gb is not None else 8.0,
            "cpu_percent": cpu_percent if cpu_percent is not None else 25.0,
            "used_ram_percent": used_ram_percent if used_ram_percent is not None else 50.0,
        }

    def clear_mock_metrics(self) -> None:
        self._mock_metrics = None

    def get_metrics(self) -> dict[str, float]:
        if self._mock_metrics is not None:
            return dict(self._mock_metrics)

        free_ram_gb = 4.0
        used_ram_percent = 50.0
        cpu_percent = 20.0

        try:
            import psutil
            mem = psutil.virtual_memory()
            free_ram_gb = round(mem.available / (1024 ** 3), 2)
            used_ram_percent = mem.percent
            cpu_percent = psutil.cpu_percent(interval=None)
        except Exception:
            pass

        return {
            "free_ram_gb": free_ram_gb,
            "used_ram_percent": used_ram_percent,
            "cpu_percent": cpu_percent,
            "timestamp": time.time(),
        }

    def is_safe_to_admit(self, profile: HardwareProfile) -> tuple[bool, str]:
        metrics = self.get_metrics()
        free_ram = metrics["free_ram_gb"]
        cpu_pct = metrics["cpu_percent"]

        if free_ram < profile.safe_ram_headroom_gb:
            return (
                False,
                f"Insufficient RAM headroom: {free_ram:.1f}GB available < required {profile.safe_ram_headroom_gb:.1f}GB",
            )

        if cpu_pct > profile.max_cpu_percent_threshold:
            return (
                False,
                f"CPU utilization too high: {cpu_pct:.1f}% > threshold {profile.max_cpu_percent_threshold:.1f}%",
            )

        return (True, "Host operating parameters are healthy")

    def is_critical_redline(self) -> bool:
        """Returns True if host is in danger of OOM or CPU freeze."""
        metrics = self.get_metrics()
        return metrics["free_ram_gb"] < 0.6 or metrics["cpu_percent"] > 95.0

