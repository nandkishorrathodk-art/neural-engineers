from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, Field

from neural.constants import HealthState
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.observability.model_health")


class HealthReport(BaseModel):
    state: HealthState
    circuit_breaker_open: bool
    total_calls: int
    consecutive_failures: int
    timeout_count: int
    avg_latency_ms: float
    error_rate: float
    estimated_memory_mb: float = Field(default=128.0)
    uptime_seconds: float


class ModelHealthMonitor:
    """
    Health, memory & timeout monitor for System 1 Laya (Section 13).
    Implements circuit-breaking protection against latency spikes or model hangs.
    """

    def __init__(
        self,
        sla_timeout_ms: float = 40.0,
        max_consecutive_failures: int = 5,
        max_error_rate: float = 0.20,
    ) -> None:
        self.sla_timeout_ms = sla_timeout_ms
        self.max_consecutive_failures = max_consecutive_failures
        self.max_error_rate = max_error_rate

        self.start_time = time.time()
        self.total_calls = 0
        self.total_errors = 0
        self.consecutive_failures = 0
        self.timeout_count = 0
        self.total_latency_ms = 0.0
        self.circuit_breaker_open = False

    def record_call(self, latency_ms: float, is_error: bool = False) -> None:
        self.total_calls += 1
        self.total_latency_ms += latency_ms

        if latency_ms > self.sla_timeout_ms:
            self.timeout_count += 1

        if is_error:
            self.total_errors += 1
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0

        # Circuit breaker trigger
        err_rate = (self.total_errors / self.total_calls) if self.total_calls > 0 else 0.0
        if (
            self.consecutive_failures >= self.max_consecutive_failures
            or (self.total_calls >= 10 and err_rate > self.max_error_rate)
        ):
            if not self.circuit_breaker_open:
                logger.error(
                    "System 1 Circuit Breaker TRIPPED: opening circuit",
                    consecutive_failures=self.consecutive_failures,
                    error_rate=err_rate,
                )
                self.circuit_breaker_open = True

    def reset_circuit_breaker(self) -> None:
        self.circuit_breaker_open = False
        self.consecutive_failures = 0
        logger.info("System 1 Circuit Breaker RESET to CLOSED")

    def get_report(self) -> HealthReport:
        uptime = time.time() - self.start_time
        avg_lat = (self.total_latency_ms / self.total_calls) if self.total_calls > 0 else 0.0
        err_rate = (self.total_errors / self.total_calls) if self.total_calls > 0 else 0.0

        if self.circuit_breaker_open:
            state = HealthState.UNHEALTHY
        elif err_rate > 0.05 or self.timeout_count > (self.total_calls * 0.1):
            state = HealthState.DEGRADED
        else:
            state = HealthState.HEALTHY

        return HealthReport(
            state=state,
            circuit_breaker_open=self.circuit_breaker_open,
            total_calls=self.total_calls,
            consecutive_failures=self.consecutive_failures,
            timeout_count=self.timeout_count,
            avg_latency_ms=round(avg_lat, 2),
            error_rate=round(err_rate, 4),
            uptime_seconds=round(uptime, 2),
        )


_health_monitor_instance: Optional[ModelHealthMonitor] = None


def get_model_health_monitor() -> ModelHealthMonitor:
    global _health_monitor_instance
    if _health_monitor_instance is None:
        _health_monitor_instance = ModelHealthMonitor()
    return _health_monitor_instance


def reset_model_health_monitor() -> None:
    global _health_monitor_instance
    _health_monitor_instance = None
