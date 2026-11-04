from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timezone
from typing import Any

from neural.config import get_settings
from neural.logger import get_logger
from neural.security.kill_switch import get_kill_switch

logger = get_logger("ancos.observability.telemetry")


class SystemTelemetryCollector:
    """
    Collects real-time OS and runtime telemetry feeds (CPU, Memory, Disk, Network)
    for dashboard telemetry gauges, monitoring, and health diagnostics.
    Employs robust standard-library methods for seamless cross-platform execution.
    """

    @classmethod
    def get_cpu_info(cls) -> dict[str, Any]:
        """Gathers CPU core count and current system utilization estimate."""
        cores = os.cpu_count() or 4
        # Try psutil if installed
        try:
            import psutil  # type: ignore
            percent = float(psutil.cpu_percent(interval=0.05))
            return {"percent": round(percent, 1), "cores": cores, "available": True}
        except ImportError:
            pass

        # A single /proc/stat sample is cumulative since boot, not current load.
        # Report unknown until a real sampler is available instead of guessing.
        return {"percent": None, "cores": cores, "available": False}

    @classmethod
    def get_memory_info(cls) -> dict[str, Any]:
        """Gathers total, used, and percentage system RAM metrics."""
        try:
            import psutil  # type: ignore
            vm = psutil.virtual_memory()
            return {
                "total_mb": round(vm.total / (1024 * 1024), 1),
                "used_mb": round(vm.used / (1024 * 1024), 1),
                "percent": round(vm.percent, 1),
                "available": True,
            }
        except ImportError:
            pass

        # Linux /proc/meminfo fallback
        if sys.platform.startswith("linux") and os.path.exists("/proc/meminfo"):
            try:
                mem: dict[str, float] = {}
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            val = parts[1].strip().split()[0]
                            mem[parts[0].strip()] = float(val)
                total_kb = mem.get("MemTotal")
                free_kb = mem.get("MemAvailable", mem.get("MemFree"))
                if not total_kb or free_kb is None or free_kb < 0 or free_kb > total_kb:
                    raise ValueError("Memory counters are incomplete or invalid")
                used_kb = total_kb - free_kb
                pct = (used_kb / total_kb) * 100.0
                return {
                    "total_mb": round(total_kb / 1024, 1),
                    "used_mb": round(used_kb / 1024, 1),
                    "percent": round(pct, 1),
                    "available": True,
                }
            except Exception:
                pass

        # Unknown is safer than presenting nominal capacity as measured usage.
        return {
            "total_mb": None,
            "used_mb": None,
            "percent": None,
            "available": False,
        }

    @classmethod
    def get_disk_info(cls, target_path: str | None = None) -> dict[str, Any]:
        """Calculates storage capacity and utilization for target partition."""
        settings = get_settings()
        path = target_path or os.path.abspath(settings.EMPLOYEE_WORKSPACE_BASE)
        try:
            total, used, free = shutil.disk_usage(path)
            pct = (used / total) * 100.0 if total > 0 else 0.0
            return {
                "total_gb": round(total / (1024 * 1024 * 1024), 1),
                "used_gb": round(used / (1024 * 1024 * 1024), 1),
                "free_gb": round(free / (1024 * 1024 * 1024), 1),
                "percent": round(pct, 1),
                "path": path,
                "available": True,
            }
        except Exception as e:
            logger.warning("Failed to inspect disk usage", path=path, error=str(e))
            return {
                "total_gb": None,
                "used_gb": None,
                "free_gb": None,
                "percent": None,
                "path": path,
                "available": False,
            }

    @classmethod
    def get_network_info(cls) -> dict[str, Any]:
        """Gathers aggregate network throughput statistics."""
        try:
            import psutil  # type: ignore
            net = psutil.net_io_counters()
            return {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
                "available": True,
            }
        except ImportError:
            pass

        return {
            "bytes_sent": None,
            "bytes_recv": None,
            "packets_sent": None,
            "packets_recv": None,
            "available": False,
        }

    @classmethod
    def collect_all_telemetry(cls) -> dict[str, Any]:
        """Compiles full operational telemetry snapshot for control room dashboard gauges."""
        settings = get_settings()
        kill_switch = get_kill_switch()

        cpu = cls.get_cpu_info()
        mem = cls.get_memory_info()
        disk = cls.get_disk_info()
        net = cls.get_network_info()

        metrics = (cpu, mem, disk, net)
        available = any(bool(metric.get("available")) for metric in metrics)
        return {
            "cpu": cpu,
            "memory": mem,
            "disk": disk,
            "network": net,
            "available": available,
            "workstations": {
                # The process-local collector cannot know broker-backed sessions.
                "active": None,
                "max": settings.MAX_CONCURRENT_EMPLOYEES,
                "available": False,
            },
            "kill_switch": {
                "active": kill_switch.is_halted,
                "reason": kill_switch.halt_reason,
                "halted_by": kill_switch.halted_by,
                "timestamp": kill_switch.halt_timestamp.isoformat() if kill_switch.halt_timestamp else None,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

