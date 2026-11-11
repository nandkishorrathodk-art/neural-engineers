from __future__ import annotations

from typing import Any, Optional

from neural.db.models import Employee
from neural.db.repositories import AuditRepository, EmployeeRepository
from neural.logger import get_logger

logger = get_logger("ancos.employees.promotion")


class EmployeePromotionManager:
    """
    Empirical Employee Promotion & Career Progression Manager.
    Enforces merit-based promotion gates:
    - Evaluates accumulated verified tasks, certified skills, and quality/pass rates.
    - Transitions employee job titles, model configuration tiers, and tool capabilities.
    - Preserves audit trails of all promotion decisions.
    """

    def __init__(
        self,
        employee_repo: EmployeeRepository,
        audit_repo: Optional[AuditRepository] = None,
    ) -> None:
        self.employee_repo = employee_repo
        self.audit_repo = audit_repo

    async def evaluate_eligibility(
        self,
        employee_id: str,
        min_verified_tasks: int = 1,
        min_verified_skills: int = 1,
        min_pass_rate: float = 0.5,
    ) -> dict[str, Any]:
        """
        Assesses whether an employee has earned promotion eligibility
        based on empirical task verification and skill certificates.
        """
        return await self.employee_repo.evaluate_promotion_eligibility(
            employee_id=employee_id,
            min_verified_tasks=min_verified_tasks,
            min_verified_skills=min_verified_skills,
            min_pass_rate=min_pass_rate,
        )

    async def promote(
        self,
        employee_id: str,
        new_title: str,
        new_model_tier: Optional[str] = None,
        additional_tools: Optional[list[str]] = None,
        reason: Optional[str] = None,
        actor: str = "BossAI",
        require_eligibility: bool = True,
        min_verified_tasks: int = 1,
        min_verified_skills: int = 1,
        min_pass_rate: float = 0.5,
    ) -> Employee:
        """
        Applies a merit-based promotion to an employee, updating their title,
        model tier, and tool permissions, while appending to the audit log.
        """
        if require_eligibility:
            assessment = await self.evaluate_eligibility(
                employee_id=employee_id,
                min_verified_tasks=min_verified_tasks,
                min_verified_skills=min_verified_skills,
                min_pass_rate=min_pass_rate,
            )
            if not assessment["eligible"]:
                raise ValueError(
                    f"Employee '{employee_id}' is not eligible for promotion. "
                    f"Verified tasks: {assessment['verified_tasks_count']} (min {assessment['criteria']['min_verified_tasks']}), "
                    f"Tasks since last promotion: {assessment.get('tasks_since_last_promotion', 0)}, "
                    f"Verified skills: {assessment['verified_skills_count']} (min {assessment['criteria']['min_verified_skills']}), "
                    f"Pass rate: {assessment['pass_rate']} (min {assessment['criteria']['min_pass_rate']})."
                )

        promoted = await self.employee_repo.promote_employee(
            employee_id=employee_id,
            new_title=new_title,
            new_model_tier=new_model_tier,
            additional_tools=additional_tools,
            reason=reason,
        )

        logger.info(
            "Employee promoted successfully",
            employee_id=promoted.employee_id,
            new_title=new_title,
            model_tier=new_model_tier,
            tools=additional_tools,
            actor=actor,
        )

        if self.audit_repo:
            await self.audit_repo.log(
                actor=actor,
                action="PROMOTE_EMPLOYEE",
                resource_type="Employee",
                resource_id=promoted.employee_id,
                details={
                    "employee_id": promoted.employee_id,
                    "new_title": new_title,
                    "new_model_tier": new_model_tier,
                    "additional_tools": additional_tools or [],
                    "reason": reason or "Empirical merit promotion",
                },
            )

        return promoted

