from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from neural.constants import SkillLevel, TaskStatus
from neural.db.models import Employee, Project, Task
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository
from neural.logger import get_logger

logger = get_logger("ancos.evolution.gap_analysis")


@dataclass
class SkillGap:
    gap_id: str
    skill_name: str
    current_level: str | None
    target_level: str
    urgency: str  # critical, high, medium, low
    reason: str
    affected_employee_ids: list[str] = field(default_factory=list)
    source_task_ids: list[str] = field(default_factory=list)
    source_project_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillGapReport:
    report_id: str
    total_gaps: int
    critical_count: int
    gaps: list[SkillGap]
    recommendations: list[dict[str, Any]]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "total_gaps": self.total_gaps,
            "critical_count": self.critical_count,
            "gaps": [g.to_dict() for g in self.gaps],
            "recommendations": self.recommendations,
            "generated_at": self.generated_at,
        }


class SkillGapAnalyzer:
    """
    Autonomous skill gap analysis engine.
    Detects capability deficits from failed tasks, blocked workflows,
    and strategic project requirements against live workforce verified skills.
    """

    def __init__(
        self,
        employee_repo: EmployeeRepository | None = None,
        task_repo: TaskRepository | None = None,
        project_repo: ProjectRepository | None = None,
    ):
        self.employee_repo = employee_repo
        self.task_repo = task_repo
        self.project_repo = project_repo

    async def analyze_gaps(
        self,
        failed_tasks: Sequence[Task] | None = None,
        active_projects: Sequence[Project] | None = None,
        employees: Sequence[Employee] | None = None,
    ) -> SkillGapReport:
        report_id = f"GAP-{uuid.uuid4().hex[:8].upper()}"
        now_iso = datetime.now(UTC).isoformat()

        # 1. Gather entities if not directly provided
        all_employees = list(employees or [])
        if not all_employees and self.employee_repo:
            all_employees = list(await self.employee_repo.list_all())

        all_failed_tasks = list(failed_tasks or [])
        if not all_failed_tasks and self.task_repo:
            all_failed_tasks = list(await self.task_repo.list_by_status(TaskStatus.FAILED))

        all_projects = list(active_projects or [])
        if not all_projects and self.project_repo:
            all_projects = list(await self.project_repo.list_all())

        # Build workforce verified skills map: skill_name -> list of (emp_id, level)
        workforce_verified: dict[str, list[tuple[str, str]]] = {}
        emp_skills_lookup: dict[str, dict[str, tuple[bool, str]]] = {}

        for emp in all_employees:
            emp_skills = {}
            for s in emp.skills:
                lvl = s.level.value if hasattr(s.level, "value") else str(s.level)
                emp_skills[s.skill_name.lower()] = (s.verified, lvl)
                if s.verified:
                    workforce_verified.setdefault(s.skill_name.lower(), []).append((emp.employee_id, lvl))
            emp_skills_lookup[emp.employee_id] = emp_skills
            emp_skills_lookup[emp.id] = emp_skills

        gaps: list[SkillGap] = []
        seen_gaps: set[str] = set()

        # 2. Analyze Task Failures
        for task in all_failed_tasks:
            required = task.required_capabilities or []
            assignee_emp_id = None
            if task.assignee:
                assignee_emp_id = task.assignee.employee_id
            elif task.assigned_to:
                # Find employee by UUID or ID
                match = next((e for e in all_employees if e.id == task.assigned_to or e.employee_id == task.assigned_to), None)
                if match:
                    assignee_emp_id = match.employee_id

            for cap in required:
                cap_lower = cap.lower()
                gap_key = f"fail:{cap_lower}"
                if gap_key in seen_gaps:
                    continue

                # Check if assignee lacked verified skill
                has_verified = False
                curr_lvl = None
                if assignee_emp_id and assignee_emp_id in emp_skills_lookup:
                    verified_tuple = emp_skills_lookup[assignee_emp_id].get(cap_lower)
                    if verified_tuple:
                        has_verified, curr_lvl = verified_tuple

                if not has_verified:
                    seen_gaps.add(gap_key)
                    gaps.append(
                        SkillGap(
                            gap_id=f"GAP-FAIL-{uuid.uuid4().hex[:6].upper()}",
                            skill_name=cap,
                            current_level=curr_lvl,
                            target_level=SkillLevel.COMPETENT.value,
                            urgency="critical",
                            reason=f"Task {task.task_id} failed while requiring capability '{cap}'. Assignee has no verified proof.",
                            affected_employee_ids=[assignee_emp_id] if assignee_emp_id else [],
                            source_task_ids=[task.task_id],
                            source_project_ids=[task.project_id] if task.project_id else [],
                        )
                    )

        # 3. Analyze Strategic Projects & Scheduled Tasks
        tasks_to_check: list[Task] = []
        if self.task_repo:
            for st in (TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.ASSIGNED):
                tasks_to_check.extend(await self.task_repo.list_by_status(st))
        for proj in all_projects:
            proj_tasks = proj.__dict__.get("tasks")
            if proj_tasks:
                for t in proj_tasks:
                    if t not in tasks_to_check and t.status in (TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.ASSIGNED):
                        tasks_to_check.append(t)


        for t in tasks_to_check:
            for cap in t.required_capabilities or []:
                cap_lower = cap.lower()
                gap_key = f"proj:{cap_lower}"
                if gap_key in seen_gaps:
                    continue

                verified_holders = workforce_verified.get(cap_lower, [])
                if not verified_holders:
                    seen_gaps.add(gap_key)
                    gaps.append(
                        SkillGap(
                            gap_id=f"GAP-PROJ-{uuid.uuid4().hex[:6].upper()}",
                            skill_name=cap,
                            current_level=None,
                            target_level=SkillLevel.COMPETENT.value,
                            urgency="high",
                            reason=f"Task {t.task_id} requires '{cap}', but zero workforce employees hold verified certification.",
                            affected_employee_ids=[],
                            source_task_ids=[t.task_id],
                            source_project_ids=[t.project_id] if t.project_id else [],
                        )
                    )


        # 4. Generate Actionable Recommendations
        recommendations = []
        for g in gaps:
            if g.urgency == "critical":
                recommendations.append({
                    "action": "targeted_curriculum_and_certification",
                    "skill_name": g.skill_name,
                    "target_level": g.target_level,
                    "affected_employees": g.affected_employee_ids,
                    "rationale": f"Resolve active production failure in task(s) {', '.join(g.source_task_ids)}",
                })
            else:
                recommendations.append({
                    "action": "workforce_upskilling_or_recruitment",
                    "skill_name": g.skill_name,
                    "target_level": g.target_level,
                    "rationale": f"Unblock scheduled tasks in project(s) {', '.join(g.source_project_ids)}",
                })

        critical_count = sum(1 for g in gaps if g.urgency == "critical")
        logger.info(
            "Completed skill gap analysis",
            report_id=report_id,
            total_gaps=len(gaps),
            critical=critical_count,
        )

        return SkillGapReport(
            report_id=report_id,
            total_gaps=len(gaps),
            critical_count=critical_count,
            gaps=gaps,
            recommendations=recommendations,
            generated_at=now_iso,
        )
