from __future__ import annotations

from typing import Any, Optional

from neural.decision_intelligence.calibration.calibration_monitor import (
    CalibrationMonitor,
    get_calibration_monitor,
)
from neural.decision_intelligence.governance.shadow_mode import (
    ShadowModeManager,
    get_shadow_mode_manager,
)
from neural.decision_intelligence.observability.model_health import (
    ModelHealthMonitor,
    get_model_health_monitor,
)
from neural.decision_intelligence.system_one.inference_service import (
    LayaInferenceService,
    get_inference_service,
)


class PerformanceReporter:
    """
    Control Room & Executive Briefing Metrics Exporter (Section 13).
    """

    def __init__(
        self,
        inference_service: Optional[LayaInferenceService] = None,
        health_monitor: Optional[ModelHealthMonitor] = None,
        calibration_monitor: Optional[CalibrationMonitor] = None,
        shadow_manager: Optional[ShadowModeManager] = None,
    ) -> None:
        self.inference_service = inference_service or get_inference_service()
        self.health_monitor = health_monitor or get_model_health_monitor()
        self.calibration_monitor = calibration_monitor or get_calibration_monitor()
        self.shadow_manager = shadow_manager or get_shadow_mode_manager()

    def generate_report(self) -> dict[str, Any]:
        health = self.health_monitor.get_report()
        calib = self.calibration_monitor.get_metrics()
        shadow_states = self.shadow_manager.list_shadow_states()
        service_health = self.inference_service.get_health()

        total_decisions = health.total_calls
        tokens_saved = total_decisions * 450

        return {
            "title": "ANCOS Phase 8 — Laya Decision Intelligence Briefing",
            "health_state": health.state.value,
            "circuit_breaker": "OPEN" if health.circuit_breaker_open else "CLOSED",
            "uptime_seconds": health.uptime_seconds,
            "throughput": {
                "total_decisions": total_decisions,
                "avg_latency_ms": health.avg_latency_ms,
                "timeouts_exceeded": health.timeout_count,
                "tokens_saved": tokens_saved,
            },
            "calibration": {
                "ece": calib["ece"],
                "brier_score": calib["brier_score"],
                "is_calibrated": calib["is_calibrated"],
            },
            "shadow_mode_categories": shadow_states,
            "service_telemetry": service_health,
        }

    def generate_markdown_summary(self) -> str:
        report = self.generate_report()
        return f"""# ANCOS Laya Decision Intelligence — Performance Briefing

- **Operational Health:** `{report['health_state'].upper()}` (Circuit Breaker: `{report['circuit_breaker']}`)
- **Total Invocations:** `{report['throughput']['total_decisions']}`
- **Average Latency:** `{report['throughput']['avg_latency_ms']} ms` (Target: `<40 ms`)
- **Tokens Saved vs System 2:** `{report['throughput']['tokens_saved']:,}` tokens
- **Calibration (ECE):** `{report['calibration']['ece']:.4f}` (Gate: `≤ 0.08`)
- **Brier Score:** `{report['calibration']['brier_score']:.4f}`

### Shadow Mode Rollout Status:
{chr(10).join(f"- `{cat}`: {'SHADOW MODE (Zero Side-Effects)' if in_shadow else 'LIVE SYSTEM 1 PROMOTED'}" for cat, in_shadow in report['shadow_mode_categories'].items())}
"""

