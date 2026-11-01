from __future__ import annotations

from typing import Any, Optional

from neural.gui_intelligence.benchmarks.human_speed import HumanSpeedComparisonResult
from neural.gui_intelligence.benchmarks.osworld_runner import OSWorldTaskResult
from neural.gui_intelligence.benchmarks.success_metrics import SubsystemMetricsSummary


class PerformanceReporter:
    """
    Performance Report & Certification Export Generator (Section 18).
    Synthesizes markdown executive reports and structured JSON exports
    combining OSWorld benchmark results, HumanSpeed 4x comparators, and subsystem telemetry.
    """

    @staticmethod
    def generate_markdown_report(
        osworld_results: list[OSWorldTaskResult],
        speedup_results: list[HumanSpeedComparisonResult],
        metrics_summary: Optional[SubsystemMetricsSummary] = None,
        title: str = "ANCOS Phase 7 GUI Intelligence Benchmark Report",
    ) -> str:
        """Generates a complete markdown benchmark report."""
        lines = [
            f"# {title}",
            "",
            "## 1. Executive Summary & Speedup Objective",
            "",
            "| Task ID | Task Name | Human Time (s) | ANCOS Time (s) | Speedup Ratio | Meets 4× Target | Status |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        total_human = 0.0
        total_ancos = 0.0
        qualified_tasks = 0

        for s in speedup_results:
            status_badge = "✅ PASSED" if s.passed_verification and not s.disqualified else f"❌ {s.disqualification_reason}"
            meets_badge = "🚀 YES (≥4×)" if s.meets_4x_target else "⚠️ NO (<4×)"
            lines.append(
                f"| `{s.task_id}` | {s.task_name} | {s.human_time_seconds:.1f}s | {s.ancos_time_seconds:.1f}s | **{s.speedup_ratio:.2f}×** | {meets_badge} | {status_badge} |"
            )
            if not s.disqualified:
                total_human += s.human_time_seconds
                total_ancos += s.ancos_time_seconds
                qualified_tasks += 1

        overall_speedup = (
            round(total_human / total_ancos, 2)
            if total_ancos > 0 and qualified_tasks > 0
            else 0.0
        )
        lines.append("")
        lines.append(f"**Overall Aggregate Speedup on Verified Workflows:** **{overall_speedup:.2f}×**")
        lines.append("")

        lines.extend(
            [
                "## 2. OSWorld 2.1 Benchmark Compliance",
                "",
                "| Task ID | Category | Passed | Score | Steps | Avg Latency (ms) |",
                "| :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
        )

        for o in osworld_results:
            p_badge = "✅ PASS" if o.passed else "❌ FAIL"
            lines.append(
                f"| `{o.task_id}` | {o.category} | {p_badge} | {o.score:.2f} | {o.total_steps} | {o.average_step_latency_ms:.1f}ms |"
            )

        if metrics_summary:
            lines.extend(
                [
                    "",
                    "## 3. Subsystem Turn Latency & Resource Decomposition",
                    "",
                    f"- **Total Actions Dispatched:** {metrics_summary.total_actions}",
                    f"- **Average Turn Latency:** `{metrics_summary.average_turn_latency_ms:.1f}ms` (Target Budget: ≤ 300ms)",
                    f"- **Average Screen Capture:** `{metrics_summary.average_capture_ms:.1f}ms` (Target: ≤ 15ms)",
                    f"- **Average Grounding Latency:** `{metrics_summary.average_grounding_ms:.1f}ms` (Target: ≤ 35ms)",
                    f"- **Average Motor Trajectory:** `{metrics_summary.average_motor_ms:.1f}ms` (Target: 180ms - 280ms)",
                    f"- **Fast Brain Invocation Ratio:** `{metrics_summary.fast_brain_ratio * 100.0:.1f}%`",
                    f"- **System 2 Escalation Count:** `{metrics_summary.escalation_count}`",
                    f"- **300ms Turn Budget Compliance:** {'✅ COMPLIANT' if metrics_summary.compliance_with_300ms_budget else '❌ NON-COMPLIANT'}",
                ]
            )

        return "\n".join(lines)

    @staticmethod
    def generate_json_export(
        osworld_results: list[OSWorldTaskResult],
        speedup_results: list[HumanSpeedComparisonResult],
        metrics_summary: Optional[SubsystemMetricsSummary] = None,
    ) -> dict[str, Any]:
        """Generates machine-readable benchmark JSON export."""
        return {
            "osworld_tasks": [r.to_dict() for r in osworld_results],
            "speedup_benchmarks": [s.to_dict() for s in speedup_results],
            "metrics_summary": metrics_summary.to_dict() if metrics_summary else None,
        }
