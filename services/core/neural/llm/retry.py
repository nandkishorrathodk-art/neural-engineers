import asyncio
import random
import time
from enum import Enum
from typing import Any, Callable, Coroutine, Optional, Type

from neural.logger import get_logger

logger = get_logger("ancos.llm.retry")


class CircuitState(str, Enum):
    CLOSED = "closed"        # Normal operation: traffic flows through
    OPEN = "open"            # Failing: fast-fail or skip to fallback
    HALF_OPEN = "half_open"  # Trial period: test single request to see if provider recovered


class CircuitBreaker:
    """
    Circuit Breaker for AI Model Providers.
    Protects against cascading timeouts, rate limit thrashing, and persistent provider outages.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        half_open_trials: int = 1
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_trials = half_open_trials

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time: float = 0.0
        self._half_open_trial_count = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed to transition to HALF_OPEN
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                logger.info(
                    "Circuit breaker recovery timeout reached; transitioning to HALF_OPEN",
                    breaker=self.name,
                    elapsed=time.monotonic() - self._last_failure_time
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_trial_count = 0
        return self._state

    def can_execute(self) -> bool:
        """Determines if a request is allowed to pass through."""
        current_state = self.state
        if current_state == CircuitState.CLOSED:
            return True
        if current_state == CircuitState.HALF_OPEN:
            if self._half_open_trial_count < self.half_open_trials:
                self._half_open_trial_count += 1
                return True
            return False
        # OPEN
        return False

    def record_success(self) -> None:
        """Records successful execution, resetting failure metrics and closing circuit."""
        if self._state != CircuitState.CLOSED:
            logger.info("Circuit breaker closed after verified recovery", breaker=self.name)
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_trial_count = 0

    def record_failure(self, error: Optional[Exception] = None) -> None:
        """Records provider failure. If failure threshold exceeded, trips circuit to OPEN."""
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()


        if self._state == CircuitState.HALF_OPEN:
            logger.warning(
                "Trial request failed in HALF_OPEN state; re-tripping circuit to OPEN",
                breaker=self.name,
                error=str(error) if error else "unknown"
            )
            self._state = CircuitState.OPEN
        elif self._consecutive_failures >= self.failure_threshold:
            logger.warning(
                "Circuit breaker tripped OPEN due to consecutive failures",
                breaker=self.name,
                consecutive_failures=self._consecutive_failures,
                error=str(error) if error else "threshold exceeded"
            )
            self._state = CircuitState.OPEN


async def retry_with_backoff(
    operation: Callable[[], Coroutine[Any, Any, Any]],
    max_retries: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay: float = 10.0,
    jitter: float = 0.1,
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,)
) -> Any:
    """
    Executes an async operation with exponential backoff and jitter.
    """
    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await operation()
        except retryable_exceptions as err:
            last_exception = err
            if attempt >= max_retries:
                logger.error(
                    "All retry attempts exhausted for operation",
                    attempt=attempt,
                    max_retries=max_retries,
                    error=str(err)
                )
                raise err

            delay = min(max_delay, initial_delay * (backoff_factor ** attempt))
            delay += random.uniform(0, jitter)

            logger.warning(
                "Operation failed; scheduling retry with backoff",
                attempt=attempt + 1,
                max_retries=max_retries,
                delay_seconds=round(delay, 3),
                error=str(err)
            )
            await asyncio.sleep(delay)

    if last_exception:
        raise last_exception

