from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from neural.constants import ApprovalStatus, ContainerStatus, TaskStatus
from neural.db.models import Approval, AuditLog, Container, Task
from neural.observability.telemetry import SystemTelemetryCollector
from neural.security.kill_switch import get_kill_switch


class PrometheusExporter:
    """
    Exports ANCOS enterprise metrics in standard Prometheus exposition format.
    Exposes metrics for:
    - Task counts partitioned by state (pending, running, completed, failed, blocked)
    - Active employee desktop containers
    - Pending human sovereignty approval gates
    - Global emergency stop kill switch status
    - System resource ratios (CPU, RAM, Disk)
    - Total tamper-evident audit records recorded
    """

    @classmethod
    async def collect_metrics_text(cls, session: Optional[AsyncSession] = None) -> str:
        """Asynchronously compiles and formats Prometheus exposition text."""
        kill_switch = get_kill_switch()
        telemetry = SystemTelemetryCollector.collect_all_telemetry()

        # Gather database counts if session available
        task_counts: dict[str, int] = {
            TaskStatus.PENDING.value: 0,
            TaskStatus.RUNNING.value: 0,
            TaskStatus.COMPLETED.value: 0,
            TaskStatus.FAILED.value: 0,
            TaskStatus.BLOCKED.value: 0,
        }
        active_containers = 0
        pending_approvals = 0
        total_audit_records = 0

        if session:
            try:
                # Tasks by status
                task_stmt = select(Task.status)
                task_res = await session.execute(task_stmt)
                for (st,) in task_res.all():
                    val = st.value if hasattr(st, "value") else str(st)
                    task_counts[val] = task_counts.get(val, 0) + 1
            except Exception:
                pass

            try:
                # Active containers
                cnt_stmt = select(Container).where(Container.status == ContainerStatus.RUNNING)
                cnt_res = await session.execute(cnt_stmt)
                active_containers = len(cnt_res.scalars().all())
            except Exception:
                pass

            try:
                # Pending approvals
                app_stmt = select(Approval).where(Approval.status == ApprovalStatus.PENDING)
                app_res = await session.execute(app_stmt)
                pending_approvals = len(app_res.scalars().all())
            except Exception:
                pass

            try:
                # Total audit entries
                aud_stmt = select(AuditLog)
                aud_res = await session.execute(aud_stmt)
                total_audit_records = len(aud_res.scalars().all())
            except Exception:
                pass

        # Build Prometheus lines
        lines: list[str] = [
            "# HELP ancos_emergency_kill_switch_active Global emergency stop switch state (1=HALTED, 0=NOMINAL)",
            "# TYPE ancos_emergency_kill_switch_active gauge",
            f"ancos_emergency_kill_switch_active {1 if kill_switch.is_halted else 0}",
            "",
            "# HELP ancos_system_cpu_usage_ratio System CPU utilization ratio (0.0 to 1.0)",
            "# TYPE ancos_system_cpu_usage_ratio gauge",
            f"ancos_system_cpu_usage_ratio {round(telemetry['cpu']['percent'] / 100.0, 4)}",
            "",
            "# HELP ancos_system_memory_usage_ratio System memory utilization ratio (0.0 to 1.0)",
            "# TYPE ancos_system_memory_usage_ratio gauge",
            f"ancos_system_memory_usage_ratio {round(telemetry['memory']['percent'] / 100.0, 4)}",
            "",
            "# HELP ancos_system_disk_usage_ratio Workspace partition disk utilization ratio (0.0 to 1.0)",
            "# TYPE ancos_system_disk_usage_ratio gauge",
            f"ancos_system_disk_usage_ratio {round(telemetry['disk']['percent'] / 100.0, 4)}",
            "",
            "# HELP ancos_tasks_total Task count by status",
            "# TYPE ancos_tasks_total gauge",
        ]

        for status, count in sorted(task_counts.items()):
            lines.append(f'ancos_tasks_total{{status="{status}"}} {count}')

        lines.extend([
            "",
            "# HELP ancos_active_containers_count Active containerized employee workstations",
            "# TYPE ancos_active_containers_count gauge",
            f"ancos_active_containers_count {active_containers}",
            "",
            "# HELP ancos_pending_approvals_count Pending human sovereignty approval gates",
            "# TYPE ancos_pending_approvals_count gauge",
            f"ancos_pending_approvals_count {pending_approvals}",
            "",
            "# HELP ancos_audit_records_total Total recorded audit trail entries",
            "# TYPE ancos_audit_records_total counter",
            f"ancos_audit_records_total {total_audit_records}",
            "",
        ])

        return "\n".join(lines) + "\n"
