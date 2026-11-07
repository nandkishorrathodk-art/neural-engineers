import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_UP, Decimal
from typing import Any, Optional

from neural.exceptions import QuotaExceededError
from neural.logger import get_logger

logger = get_logger("ancos.llm.cost")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Pricing table in USD per 1,000,000 tokens (Standard benchmark rates)
MODEL_PRICING_PER_1M = {
    # Anthropic Claude
    "claude-3-7-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},

    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "o1-preview": {"input": 15.00, "output": 60.00},
    "o1-mini": {"input": 3.00, "output": 12.00},

    # Google Gemini
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},

    # Local / Open Weights (zero token API cost)
    "qwen-2.5-coder-32b": {"input": 0.0, "output": 0.0},
    "ollama": {"input": 0.0, "output": 0.0},
    "local": {"input": 0.0, "output": 0.0},
}

DEFAULT_FALLBACK_PRICING = {"input": 1.50, "output": 6.00}


@dataclass
class UsageRecord:
    task_id: Optional[str]
    employee_id: Optional[str]
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    project_id: Optional[str] = None
    timestamp: datetime = field(default_factory=utc_now)


class CostTracker:
    """
    Precision Token Usage and USD Cost Tracking Engine.
    Tracks expenses per task, employee, project, and foundation model.
    """

    def __init__(
        self,
        pricing_table: Optional[dict[str, dict[str, float]]] = None,
        max_task_budget_usd: float = 10.00,
        max_project_budget_usd: float = 150.00,
        monthly_hard_cap_usd: float = 500.00,
        enforce_budgets: bool = True,
    ):
        for name, value in (
            ("max_task_budget_usd", max_task_budget_usd),
            ("max_project_budget_usd", max_project_budget_usd),
            ("monthly_hard_cap_usd", monthly_hard_cap_usd),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative amount")
        self.pricing_table = pricing_table or MODEL_PRICING_PER_1M
        self.records: list[UsageRecord] = []
        self.max_task_budget_usd = max_task_budget_usd
        self.max_project_budget_usd = max_project_budget_usd
        self.monthly_hard_cap_usd = monthly_hard_cap_usd
        self.enforce_budgets = enforce_budgets

    def check_budget_ceiling(
        self,
        cost_delta: float,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        """Enforces hard budget ceilings on tasks, projects, and organization total."""
        if not math.isfinite(cost_delta) or cost_delta < 0:
            raise ValueError("cost_delta must be a finite non-negative amount")
        if not self.enforce_budgets:
            return

        # 1. Total organizational cap
        total_after = self.get_total_cost() + cost_delta
        if total_after > self.monthly_hard_cap_usd:
            logger.error("Organizational monthly budget cap exceeded", current=total_after, cap=self.monthly_hard_cap_usd)
            raise QuotaExceededError(
                f"Monthly organizational budget cap of ${self.monthly_hard_cap_usd:.2f} exceeded (projected: ${total_after:.2f})"
            )

        # 2. Task budget cap
        if task_id:
            task_cost = self.get_task_cost(task_id)["cost_usd"] + cost_delta
            if task_cost > self.max_task_budget_usd:
                logger.error("Task budget ceiling exceeded", task_id=task_id, current=task_cost, cap=self.max_task_budget_usd)
                raise QuotaExceededError(
                    f"Task '{task_id}' budget ceiling of ${self.max_task_budget_usd:.2f} exceeded (projected: ${task_cost:.2f})"
                )

        # 3. Project budget cap
        if project_id:
            project_cost = self.get_project_cost(project_id)["cost_usd"] + cost_delta
            if project_cost > self.max_project_budget_usd:
                logger.error("Project budget ceiling exceeded", project_id=project_id, current=project_cost, cap=self.max_project_budget_usd)
                raise QuotaExceededError(
                    f"Project '{project_id}' budget ceiling of ${self.max_project_budget_usd:.2f} exceeded (projected: ${project_cost:.2f})"
                )

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """
        Calculates cost in USD for a given model and token counts.
        """
        if (
            not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or not isinstance(output_tokens, int)
            or isinstance(output_tokens, bool)
            or input_tokens < 0
            or output_tokens < 0
        ):
            raise ValueError("token counts must be non-negative integers")
        pricing = self._pricing_for_model(model)

        cost_in = (input_tokens / 1_000_000.0) * pricing["input"]
        cost_out = (output_tokens / 1_000_000.0) * pricing["output"]
        return round(cost_in + cost_out, 6)

    def _pricing_for_model(self, model: str) -> dict[str, float]:
        pricing = DEFAULT_FALLBACK_PRICING
        model_lower = model.lower()
        if model_lower in self.pricing_table:
            return self.pricing_table[model_lower]
        # Match longest specific substring first (e.g. gpt-4o-mini before gpt-4o).
        for key in sorted(self.pricing_table.keys(), key=len, reverse=True):
            if key in model_lower:
                return self.pricing_table[key]
        return pricing

    def quote_upper_bound(
        self,
        model: str,
        input_token_upper_bound: int,
        output_token_upper_bound: int,
        safety_multiplier: float = 2.0,
    ) -> Decimal:
        """Estimate a pre-call reserve from a conservative token-count ceiling.

        The multiplier accounts for pricing-table drift, but this is still an
        internal estimate rather than provider invoice reconciliation.
        """
        if (
            not isinstance(input_token_upper_bound, int)
            or isinstance(input_token_upper_bound, bool)
            or input_token_upper_bound < 0
            or not isinstance(output_token_upper_bound, int)
            or isinstance(output_token_upper_bound, bool)
            or output_token_upper_bound < 0
        ):
            raise ValueError("token upper bounds must be non-negative integers")
        if not math.isfinite(safety_multiplier) or safety_multiplier < 1:
            raise ValueError("safety_multiplier must be finite and at least 1")

        pricing = self._pricing_for_model(model)
        in_price = Decimal(str(pricing["input"]))
        out_price = Decimal(str(pricing["output"]))
        multiplier = Decimal(str(safety_multiplier))
        quoted = (
            Decimal(input_token_upper_bound) * in_price
            + Decimal(output_token_upper_bound) * out_price
        ) / Decimal(1_000_000) * multiplier
        rounded = quoted.quantize(Decimal("0.00000001"), rounding=ROUND_UP)
        return max(rounded, Decimal("0.00000001"))

    def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        task_id: Optional[str] = None,
        employee_id: Optional[str] = None,
        project_id: Optional[str] = None,
        cost_usd: Optional[float] = None,
        enforce_budget: bool = True,
    ) -> UsageRecord:
        """
        Records a single LLM API usage event and computes USD cost.
        Enforces budget ceilings before committing usage.
        """
        calculated_cost = self.calculate_cost(model, input_tokens, output_tokens)
        exact_cost = cost_usd if cost_usd is not None else calculated_cost
        if not isinstance(exact_cost, (int, float)) or not math.isfinite(exact_cost) or exact_cost < 0:
            raise ValueError("cost_usd must be a finite non-negative amount")
        if enforce_budget:
            self.check_budget_ceiling(exact_cost, task_id=task_id, project_id=project_id)
        record = UsageRecord(
            task_id=task_id,
            employee_id=employee_id,
            project_id=project_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=exact_cost
        )
        self.records.append(record)

        logger.info(
            "LLM API usage recorded",
            model=model,
            task_id=task_id,
            employee_id=employee_id,
            project_id=project_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=exact_cost
        )
        return record

    def get_task_cost(self, task_id: str) -> dict[str, Any]:
        """
        Aggregates token usage and USD cost for a specific task.
        """
        task_records = [r for r in self.records if r.task_id == task_id]
        total_in = sum(r.input_tokens for r in task_records)
        total_out = sum(r.output_tokens for r in task_records)
        total_cost = sum(r.cost_usd for r in task_records)

        return {
            "task_id": task_id,
            "call_count": len(task_records),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "cost_usd": round(total_cost, 6)
        }

    def get_employee_cost(self, employee_id: str) -> dict[str, Any]:
        """
        Aggregates token usage and USD cost for a specific employee.
        """
        emp_records = [r for r in self.records if r.employee_id == employee_id]
        total_in = sum(r.input_tokens for r in emp_records)
        total_out = sum(r.output_tokens for r in emp_records)
        total_cost = sum(r.cost_usd for r in emp_records)

        return {
            "employee_id": employee_id,
            "call_count": len(emp_records),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "cost_usd": round(total_cost, 6)
        }

    def get_project_cost(self, project_id: str) -> dict[str, Any]:
        """
        Aggregates token usage and USD cost for a specific project.
        """
        proj_records = [r for r in self.records if r.project_id == project_id]
        total_in = sum(r.input_tokens for r in proj_records)
        total_out = sum(r.output_tokens for r in proj_records)
        total_cost = sum(r.cost_usd for r in proj_records)

        return {
            "project_id": project_id,
            "call_count": len(proj_records),
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "cost_usd": round(total_cost, 6)
        }

    def get_all_usage_summary(self) -> dict[str, Any]:
        """
        Returns full aggregated summary across all tasks, employees, projects, and models.
        """
        total_in = sum(r.input_tokens for r in self.records)
        total_out = sum(r.output_tokens for r in self.records)
        total_cost = sum(r.cost_usd for r in self.records)

        by_model: dict[str, dict[str, Any]] = {}
        for r in self.records:
            if r.model not in by_model:
                by_model[r.model] = {"calls": 0, "tokens": 0, "cost_usd": 0.0}
            by_model[r.model]["calls"] += 1
            by_model[r.model]["tokens"] += (r.input_tokens + r.output_tokens)
            by_model[r.model]["cost_usd"] = round(by_model[r.model]["cost_usd"] + r.cost_usd, 6)

        return {
            "total_calls": len(self.records),
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "total_cost_usd": round(total_cost, 6),
            "by_model": by_model
        }

    def get_total_cost(self) -> float:
        """Total USD cost across all recorded operations."""
        return round(sum(r.cost_usd for r in self.records), 6)

    def get_total_tokens(self) -> int:
        """Total tokens across all recorded operations."""
        return sum(r.input_tokens + r.output_tokens for r in self.records)

    async def persist_to_db(
        self,
        session: Any,
        task_id: str,
        employee_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float
    ) -> None:
        """
        Persists token usage and cost metrics to the database performance table.
        """
        from neural.db.repositories import PerformanceRepository
        repo = PerformanceRepository(session)
        await repo.record(
            employee_id=employee_id,
            task_id=task_id,
            cost_tokens=input_tokens + output_tokens,
            cost_usd=cost_usd,
            outcome="success"
        )

