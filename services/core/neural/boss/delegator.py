from typing import Optional, Sequence

from neural.constants import EmployeeStatus
from neural.db.models import Employee, Task
from neural.logger import get_logger

logger = get_logger("ancos.boss.delegator")


class TaskDelegator:
    """
    Skill-Matching Task Delegator & Workforce Prioritizer.
    Matches tasks to employees following the foundational Phase 4 mandate:
    Employees with EMPIRICALLY VERIFIED skills are strictly prioritized
    over employees with only unverified or declared skills.
    """

    @staticmethod
    def score_employee_for_task(
        employee: Employee,
        required_capabilities: Sequence[str]
    ) -> tuple[int, int, int, float]:
        """
        Computes prioritization tuple for sorting candidates:
        (verified_matches, unverified_matches, total_skills, quality_score).
        A candidate with 1 verified matching skill ALWAYS ranks above
        a candidate with 0 verified matching skills (even if they have 10 unverified ones).
        """
        req_set = {c.strip().lower() for c in required_capabilities if c.strip()}
        if not req_set:
            # When task has no explicit requirements, score based on general skills and quality
            quality = float(employee.performance_summary.get("quality_score", 1.0)) if employee.performance_summary else 1.0
            return (0, 0, len(employee.skills), quality)

        verified_skills = {
            s.skill_name.strip().lower()
            for s in employee.skills
            if getattr(s, "verified", False)
        }
        all_skills = {
            s.skill_name.strip().lower()
            for s in employee.skills
        }
        unverified_skills = all_skills - verified_skills

        verified_matches = len(req_set.intersection(verified_skills))
        unverified_matches = len(req_set.intersection(unverified_skills))
        quality = float(employee.performance_summary.get("quality_score", 1.0)) if employee.performance_summary else 1.0

        return (verified_matches, unverified_matches, len(employee.skills), quality)

    def select_best_assignee(
        self,
        task: Task,
        available_employees: Sequence[Employee]
    ) -> Optional[Employee]:
        """
        Selects the optimal employee for a task, prioritizing:
        1. Employees with VERIFIED skills matching task requirements.
        2. Fallback to employees with unverified declared skills.
        3. Fallback to any available idle employee.
        """
        if not available_employees:
            return None

        # Never over-assign an employee who is already working or unavailable.
        idle_employees = [e for e in available_employees if e.status == EmployeeStatus.IDLE]
        if not idle_employees:
            return None

        required_caps = task.required_capabilities or []

        # Sort candidates descending by (verified_matches, unverified_matches, total_skills, quality_score)
        sorted_candidates = sorted(
            idle_employees,
            key=lambda emp: self.score_employee_for_task(emp, required_caps),
            reverse=True
        )

        best_match = sorted_candidates[0]
        score = self.score_employee_for_task(best_match, required_caps)

        logger.info(
            "Selected best employee for task",
            task_id=task.task_id,
            employee_id=best_match.employee_id,
            verified_matches=score[0],
            unverified_matches=score[1],
            required_skills=required_caps
        )
        return best_match
