from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Optional

from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.loops.cycle_guard")


class CycleDetectedError(Exception):
    """Raised when an intelligence loop repeats identical states or actions."""
    pass


class LoopBudgetExceededError(Exception):
    """Raised when an intelligence loop exhausts its configured token or iteration budget."""
    pass


class ControlledLoopGuard:
    """
    Guards intelligence loops against infinite recursion, cycle oscillations,
    and budget exhaustion. Enforces timeouts and records audit trails.
    """

    def __init__(
        self,
        loop_name: str,
        max_iterations: int = 10,
        timeout_seconds: float = 30.0,
        max_cost_usd: float = 5.0,
        max_tokens: int = 50000,
    ) -> None:
        self.loop_name = loop_name
        self.max_iterations = max(1, max_iterations)
        self.timeout_seconds = max(0.5, timeout_seconds)
        self.max_cost_usd = max_cost_usd
        self.max_tokens = max_tokens

        self.iteration = 0
        self.start_time = time.time()
        self.consumed_tokens = 0
        self.consumed_cost_usd = 0.0
        self._measured_token_steps = 0
        self._measured_cost_steps = 0
        self._state_hashes: list[str] = []
        self._audit_trail: list[dict[str, Any]] = []

    def compute_state_hash(self, state: Any) -> str:
        s_repr = repr(state).encode("utf-8")
        return hashlib.sha256(s_repr).hexdigest()[:16]

    def record_step(
        self,
        step_name: str,
        state: Any,
        tokens_used: Optional[int] = None,
        cost_usd: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> None:
        if tokens_used is not None and tokens_used < 0:
            raise ValueError("tokens_used cannot be negative")
        if cost_usd is not None and (not math.isfinite(cost_usd) or cost_usd < 0):
            raise ValueError("cost_usd must be a finite non-negative value")
        self.iteration += 1
        elapsed = time.time() - self.start_time
        if tokens_used is not None:
            self.consumed_tokens += tokens_used
            self._measured_token_steps += 1
        if cost_usd is not None:
            self.consumed_cost_usd += cost_usd
            self._measured_cost_steps += 1

        state_hash = self.compute_state_hash(state)

        # Cycle Detection: Check for immediate loop (period 1), oscillating loop (period 2), or repeating cycles
        if self._state_hashes:
            if state_hash == self._state_hashes[-1]:
                logger.warning("Immediate cycle detected in loop", loop=self.loop_name, state_hash=state_hash)
                raise CycleDetectedError(f"Immediate loop cycle detected at iteration {self.iteration}")
            if len(self._state_hashes) >= 2 and state_hash == self._state_hashes[-2]:
                logger.warning("Oscillating cycle detected in loop (period 2)", loop=self.loop_name, state_hash=state_hash)
                raise CycleDetectedError(f"Oscillating cycle (period 2) detected at iteration {self.iteration}")
            if self._state_hashes.count(state_hash) >= 2:
                logger.warning("Repeated state cycle detected in loop", loop=self.loop_name, state_hash=state_hash)
                raise CycleDetectedError(f"Repeated state cycle detected at iteration {self.iteration}")

        self._state_hashes.append(state_hash)

        audit_entry = {
            "iteration": self.iteration,
            "step": step_name,
            "elapsed_seconds": round(elapsed, 3),
            "state_hash": state_hash,
            "tokens_used": tokens_used,
            "cost_usd": cost_usd,
            "notes": notes,
        }
        self._audit_trail.append(audit_entry)

        # Check bounds
        if self.iteration > self.max_iterations:
            raise LoopBudgetExceededError(f"Max iterations ({self.max_iterations}) exceeded in {self.loop_name}")
        if elapsed > self.timeout_seconds:
            raise LoopBudgetExceededError(f"Timeout ({self.timeout_seconds}s) exceeded in {self.loop_name}")
        if self.consumed_tokens > self.max_tokens:
            raise LoopBudgetExceededError(f"Token budget ({self.max_tokens}) exceeded in {self.loop_name}")
        if self.consumed_cost_usd > self.max_cost_usd:
            raise LoopBudgetExceededError(f"Cost budget (${self.max_cost_usd}) exceeded in {self.loop_name}")

    def get_audit_report(self) -> dict[str, Any]:
        return {
            "loop_name": self.loop_name,
            "total_iterations": self.iteration,
            "elapsed_seconds": round(time.time() - self.start_time, 3),
            "consumed_tokens": (
                self.consumed_tokens
                if self.iteration > 0 and self._measured_token_steps == self.iteration
                else None
            ),
            "consumed_cost_usd": (
                round(self.consumed_cost_usd, 4)
                if self.iteration > 0 and self._measured_cost_steps == self.iteration
                else None
            ),
            "token_measurement_coverage": f"{self._measured_token_steps}/{self.iteration}",
            "cost_measurement_coverage": f"{self._measured_cost_steps}/{self.iteration}",
            "token_budget_enforced": self.iteration > 0 and self._measured_token_steps == self.iteration,
            "cost_budget_enforced": self.iteration > 0 and self._measured_cost_steps == self.iteration,
            "steps": self._audit_trail,
            "audit_trail": self._audit_trail,
        }

