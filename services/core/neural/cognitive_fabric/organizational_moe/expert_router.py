from __future__ import annotations

import time
from typing import Any, Optional, Sequence

from neural.cognitive_fabric.organizational_moe.capability_matcher import CapabilityMatcher
from neural.cognitive_fabric.organizational_moe.recruitment_strategy import RecruitmentStrategy
from neural.cognitive_fabric.organizational_moe.workload_selector import WorkloadSelector
from neural.db.models import Employee, Task
from neural.decision_intelligence.schemas.choice import ChoiceCandidate, ChoiceQuestion
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.moe.router")


class OrganizationalMoERouter:
    """
    Organizational Mixture of Experts (Organizational MoE) Router.
    Dynamically routes tasks across AI employees, specialist roles, and multi-stage teams.
    Integrates fast Laya System 1 neural decisions with empirical skill verification,
    workload balancing, and automatic specialist recruitment on capability gaps.
    """

    def __init__(
        self,
        capability_matcher: Optional[CapabilityMatcher] = None,
        workload_selector: Optional[WorkloadSelector] = None,
        recruitment_strategy: Optional[RecruitmentStrategy] = None,
        laya_service: Optional[Any] = None,
        employee_factory: Optional[Any] = None,
    ) -> None:
        self.matcher = capability_matcher or CapabilityMatcher()
        self.workload = workload_selector or WorkloadSelector()
        self.recruitment = recruitment_strategy or RecruitmentStrategy()
        self.laya_service = laya_service
        self.employee_factory = employee_factory

    async def route_task(
        self,
        task: Task,
        available_employees: Sequence[Employee],
        active_task_counts: Optional[dict[str, int]] = None,
        desktop_slots_available: bool = True,
        allow_recruitment: bool = True,
    ) -> dict[str, Any]:
        """
        Routes task to optimal candidate or multi-stage team.
        """
        start_time = time.perf_counter()
        req_caps = task.required_capabilities or []
        requires_gui = any(c.lower() in ("gui", "browser", "desktop", "visual", "x11") for c in req_caps)
        counts = active_task_counts or {}

        # 1. Evaluate capability fit for all available employees
        candidate_evals = []
        emp_map = {e.employee_id: e for e in available_employees}

        for emp in available_employees:
            fit = self.matcher.evaluate_employee_fit(emp, req_caps, task=task)
            c_tasks = counts.get(emp.employee_id, 0)
            adjusted = self.workload.adjust_score_for_workload(
                fit,
                emp,
                active_task_count=c_tasks,
                requires_gui=requires_gui,
                desktop_slot_available=desktop_slots_available,
            )
            candidate_evals.append(adjusted)

        # 2. Check for capability gap & trigger dynamic recruitment if needed
        recruited_employee = None
        if allow_recruitment and self.recruitment.needs_recruitment(candidate_evals, req_caps):
            logger.info("Capability gap detected; initiating on-demand recruitment", capabilities=req_caps)
            if self.employee_factory:
                recruited_employee = await self.recruitment.recruit_specialist(
                    req_caps, self.employee_factory, department="Engineering"
                )
                if recruited_employee:
                    available_employees = list(available_employees) + [recruited_employee]
                    emp_map[recruited_employee.employee_id] = recruited_employee
                    new_fit = self.matcher.evaluate_employee_fit(recruited_employee, req_caps, task=task)
                    adjusted_new = self.workload.adjust_score_for_workload(
                        new_fit,
                        recruited_employee,
                        active_task_count=0,
                        requires_gui=requires_gui,
                        desktop_slot_available=desktop_slots_available,
                    )
                    candidate_evals.append(adjusted_new)

        if not candidate_evals:
            return {
                "selected_employee": None,
                "routing_strategy": "none_available",
                "team": [],
                "scores": [],
                "latency_ms": round((time.perf_counter() - start_time) * 1000, 2),
            }

        # Sort descending by adjusted score
        candidate_evals.sort(key=lambda c: c["adjusted_score"], reverse=True)

        # 3. Fast-Path: Laya System 1 Neural Decision (if >= 2 candidates and Laya available)
        laya_decision = None
        if self.laya_service and len(candidate_evals) >= 2:
            top_candidates = candidate_evals[:min(12, len(candidate_evals))]
            try:
                question = ChoiceQuestion(
                    question_id=f"moe-route-{task.task_id}",
                    scenario=f"Route task '{task.title}' requiring capabilities: {', '.join(req_caps)}",
                    candidates=[
                        ChoiceCandidate(
                            candidate_id=c["employee_id"],
                            description=f"{c['name']} ({c['title']}): score={c['adjusted_score']}",
                        )
                        for c in top_candidates
                    ],
                )
                if hasattr(self.laya_service, "decide_choice"):
                    laya_decision = await self.laya_service.decide_choice(question, use_batching=False)
                elif hasattr(self.laya_service, "predict_choice"):
                    laya_decision = self.laya_service.predict_choice(question)
            except Exception as e:
                logger.warning("Laya fast-path routing fallback to capability score", error=str(e))
                laya_decision = None

        # Choose primary assignee:
        # If Laya selected a candidate and confidence is reasonable, use it; otherwise top ranked candidate
        primary_emp_id = candidate_evals[0]["employee_id"]
        if laya_decision and getattr(laya_decision, "choice", None):
            chosen = laya_decision.choice
            # Verify chosen candidate exists in evaluated pool
            if any(c["employee_id"] == chosen for c in candidate_evals):
                primary_emp_id = chosen

        best_employee = emp_map.get(primary_emp_id)

        # 4. Multi-stage team assembly for complex high-priority tasks
        team = [best_employee]
        if task.priority.value in ("critical", "high") and len(candidate_evals) >= 2:
            # Add secondary specialist or reviewer
            second_emp_id = candidate_evals[1]["employee_id"]
            if second_emp_id != primary_emp_id:
                team.append(emp_map.get(second_emp_id))

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return {
            "selected_employee": best_employee,
            "selected_employee_id": primary_emp_id,
            "routing_strategy": "laya_fast_path" if laya_decision else "capability_workload_rank",
            "team": team,
            "top_candidates": candidate_evals[:3],
            "recruited_new_specialist": recruited_employee is not None,
            "laya_decision": laya_decision.to_dict() if laya_decision and hasattr(laya_decision, "to_dict") else None,
            "latency_ms": duration_ms,
        }

