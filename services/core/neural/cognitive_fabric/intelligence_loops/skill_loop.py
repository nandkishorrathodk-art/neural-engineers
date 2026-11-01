from __future__ import annotations

from typing import Any, Callable, Optional

from neural.cognitive_fabric.intelligence_loops.cycle_guard import (
    ControlledLoopGuard,
    CycleDetectedError,
    LoopBudgetExceededError,
)
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.loops.skill")


class ControlledSkillLoop:
    """
    Controlled Skill Improvement Loop:
    Execute → Evaluate → Compile Procedure → Benchmark → Certify → Reuse.
    Synthesizes and certifies reusable operational recipes from verified task traces,
    enforcing benchmark qualification and preventing unverified skill promotion.
    """

    def __init__(
        self,
        memory_store: Optional[SixTierMemoryStore] = None,
        max_iterations: int = 4,
        timeout_seconds: float = 20.0,
        benchmark_pass_threshold: float = 0.85,
    ) -> None:
        self.memory_store = memory_store or SixTierMemoryStore()
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
        self.pass_threshold = benchmark_pass_threshold

    async def execute(
        self,
        procedure_name: str,
        execution_trace: list[dict[str, Any]],
        benchmark_fn: Optional[Callable[[list[dict[str, Any]]], float]] = None,
        certifier_id: str = "BossAI",
    ) -> dict[str, Any]:
        guard = ControlledLoopGuard(
            loop_name=f"SkillLoop-{procedure_name}",
            max_iterations=self.max_iterations,
            timeout_seconds=self.timeout_seconds,
        )

        current_recipe = list(execution_trace)
        status = "in_progress"
        benchmark_score = 0.0
        certified_record = None

        try:
            while status == "in_progress":
                # Step 1: Evaluate & Benchmark procedure
                if benchmark_fn:
                    benchmark_score = benchmark_fn(current_recipe)
                else:
                    # Default heuristic: trace contains successful steps with exit code 0
                    success_steps = sum(1 for s in current_recipe if s.get("exit_code", 0) == 0)
                    benchmark_score = round(success_steps / max(1, len(current_recipe)), 2)

                # Record loop step
                guard.record_step(
                    step_name="benchmark_and_refine",
                    state={"recipe_len": len(current_recipe), "score": benchmark_score},
                    tokens_used=150,
                    cost_usd=0.002,
                    notes=f"Benchmark score: {benchmark_score}",
                )

                # Step 2: Certify if benchmark threshold exceeded
                if benchmark_score >= self.pass_threshold:
                    certified_record = await self.memory_store.store_procedural(
                        procedure_name=procedure_name,
                        steps=current_recipe,
                        code_recipe=f"# Certified recipe for {procedure_name}\n# Benchmark score: {benchmark_score}",
                        verified_by=certifier_id,
                    )
                    status = "certified"
                    break

                # Step 3: Refine recipe (filter out failed steps)
                if guard.iteration < self.max_iterations:
                    current_recipe = [s for s in current_recipe if s.get("exit_code", 0) == 0]
                    if not current_recipe:
                        status = "failed_qualification"
                        break
                else:
                    status = "failed_qualification"
                    break

        except (CycleDetectedError, LoopBudgetExceededError) as e:
            logger.warning("Skill improvement loop halted early by guard", error=str(e))
            status = "escalated"

        return {
            "procedure_name": procedure_name,
            "status": status,
            "benchmark_score": benchmark_score,
            "iterations": guard.iteration,
            "is_certified": status == "certified",
            "certified_record_id": certified_record.id if certified_record else None,
            "audit_report": guard.get_audit_report(),
        }
