from datetime import UTC, datetime
from typing import Any

from neural.constants import EmployeeStatus, TaskStatus
from neural.db.repositories import (
    ActionItemRepository,
    ApprovalRepository,
    EmployeeRepository,
    MeetingRepository,
    PerformanceRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.logger import get_logger

logger = get_logger("ancos.meetings.briefings")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ExecutiveBriefingGenerator:
    """
    Autonomous Executive Briefing Generator for the Human Owner & Board.
    Synthesizes organizational telemetry: milestones, workforce health,
    critical blockers, LLM compute spend (USD), and pending human approvals.
    """

    def __init__(
        self,
        project_repo: ProjectRepository,
        task_repo: TaskRepository,
        employee_repo: EmployeeRepository,
        meeting_repo: MeetingRepository,
        perf_repo: PerformanceRepository | None = None,
        approval_repo: ApprovalRepository | None = None,
        action_item_repo: ActionItemRepository | None = None
    ):
        self.project_repo = project_repo
        self.task_repo = task_repo
        self.employee_repo = employee_repo
        self.meeting_repo = meeting_repo
        self.perf_repo = perf_repo
        self.approval_repo = approval_repo
        self.action_item_repo = action_item_repo

    async def generate_briefing(self, project_id: str | None = None) -> dict[str, Any]:
        """
        Compiles the complete executive briefing dictionary.
        """
        now = utc_now()

        # 1. Milestones & Projects
        all_projects = await self.project_repo.list_all()
        if project_id:
            projects = [p for p in all_projects if p.id == project_id or p.name == project_id]
        else:
            projects = list(all_projects)

        milestones_data: list[dict[str, Any]] = []
        for p in projects:
            tasks = await self.task_repo.list_by_project(p.id)
            completed_tasks = [t for t in tasks if t.status == TaskStatus.COMPLETED]
            blocked_tasks = [t for t in tasks if t.status == TaskStatus.BLOCKED]
            running_tasks = [t for t in tasks if t.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED)]
            total_tasks = len(tasks)
            pct = round((len(completed_tasks) / total_tasks * 100), 1) if total_tasks > 0 else 0.0

            milestones_data.append({
                "project_id": p.id,
                "project_name": p.name,
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                "total_tasks": total_tasks,
                "completed_tasks": len(completed_tasks),
                "blocked_tasks": len(blocked_tasks),
                "running_tasks": len(running_tasks),
                "completion_percentage": pct,
                "phases_count": len(p.execution_plan.get("phases", [])) if p.execution_plan else 0
            })

        # 2. Active Workforce
        all_employees = await self.employee_repo.list_all()
        total_employees = len(all_employees)
        working_count = sum(1 for e in all_employees if e.status == EmployeeStatus.WORKING)
        idle_count = sum(1 for e in all_employees if e.status == EmployeeStatus.IDLE)
        suspended_count = sum(1 for e in all_employees if e.status == EmployeeStatus.SUSPENDED)

        total_skills = 0
        verified_skills = 0
        department_counts: dict[str, int] = {}
        emp_map: dict[str, str] = {}

        for emp in all_employees:
            emp_map[emp.id] = emp.employee_id
            emp_map[emp.employee_id] = emp.employee_id
            dept = emp.department or "Engineering"
            department_counts[dept] = department_counts.get(dept, 0) + 1
            for sk in getattr(emp, "skills", []):
                total_skills += 1
                if getattr(sk, "verified", False):
                    verified_skills += 1

        workforce_data = {
            "total_employees": total_employees,
            "working": working_count,
            "idle": idle_count,
            "suspended": suspended_count,
            "department_breakdown": department_counts,
            "skills": {
                "total_declared": total_skills,
                "total_verified": verified_skills,
                "verification_rate_pct": round(verified_skills / total_skills * 100, 1) if total_skills > 0 else 0.0
            }
        }

        # 3. Blockers
        blockers_data: list[dict[str, Any]] = []
        for p in projects:
            p_tasks = await self.task_repo.list_by_project(p.id)
            for t in p_tasks:
                if t.status in (TaskStatus.BLOCKED, TaskStatus.FAILED):
                    blockers_data.append({
                        "task_id": t.task_id,
                        "title": t.title,
                        "project_name": p.name,
                        "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                        "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                        "assigned_to": emp_map.get(t.assigned_to, t.assigned_to) if t.assigned_to else None,
                        "retry_count": t.retry_count
                    })

        # 4. Financial Costs (USD & Tokens)
        cost_data: dict[str, Any] = {
            "total_tokens": 0,
            "total_usd": 0.0,
            "records_count": 0
        }
        if self.perf_repo:
            cost_metrics = await self.perf_repo.get_total_cost()
            cost_data = cost_metrics

        # 5. Pending Human Approvals
        pending_approvals: list[dict[str, Any]] = []
        if self.approval_repo:
            approvals = await self.approval_repo.list_pending()
            for a in approvals:
                pending_approvals.append({
                    "id": a.id,
                    "action_type": a.action_type,
                    "description": a.description,
                    "justification": a.justification,
                    "requested_by": a.requested_by,
                    "requested_at": a.requested_at.isoformat() if a.requested_at else None
                })

        # 6. Action Items Summary
        action_items_summary: dict[str, Any] = {
            "open_count": 0,
            "overdue_count": 0
        }
        if self.action_item_repo:
            unresolved = await self.action_item_repo.list_unresolved()
            overdue = await self.action_item_repo.list_overdue()
            action_items_summary["open_count"] = len(unresolved)
            action_items_summary["overdue_count"] = len(overdue)

        # 7. Narrative Executive Summary
        active_proj_count = sum(1 for m in milestones_data if m["status"] in ("active", "planning"))
        summary = (
            f"ANCOS Executive Briefing as of {now.strftime('%Y-%m-%d %H:%M UTC')}. "
            f"Currently overseeing {len(milestones_data)} project(s) ({active_proj_count} active) "
            f"with an active workforce of {total_employees} specialized AI employees ({verified_skills} verified skills). "
            f"Total compute cost to date is ${cost_data['total_usd']:.4f} across {cost_data['total_tokens']:,} tokens. "
            f"Currently {len(blockers_data)} active blocker(s), {action_items_summary['open_count']} open action item(s) "
            f"({action_items_summary['overdue_count']} overdue), and {len(pending_approvals)} pending human approval request(s)."
        )

        briefing = {
            "generated_at": now.isoformat(),
            "executive_summary": summary,
            "milestones": milestones_data,
            "active_workforce": workforce_data,
            "blockers": blockers_data,
            "financial_costs": cost_data,
            "pending_approvals": pending_approvals,
            "action_items_summary": action_items_summary
        }

        logger.info(
            "Compiled executive briefing",
            projects_count=len(milestones_data),
            workforce_count=total_employees,
            blockers_count=len(blockers_data),
            cost_usd=cost_data["total_usd"]
        )
        return briefing

    async def generate_markdown_briefing(self, project_id: str | None = None) -> str:
        """
        Formats executive briefing into high-level Markdown for C-suite inspection.
        """
        data = await self.generate_briefing(project_id)
        lines = [
            "# 🏢 ANCOS Autonomous Executive Briefing",
            f"**Generated**: {data['generated_at']} | **Target**: Executive Board & Human Owner",
            "",
            "## 📌 Executive Summary",
            data["executive_summary"],
            "",
            "## 🎯 Project Milestones & Delivery",
        ]

        if not data["milestones"]:
            lines.append("_No projects currently registered._")
        else:
            lines.append("| Project | Status | Completed Tasks | Blocked | Progress |")
            lines.append("| :--- | :---: | :---: | :---: | :---: |")
            for m in data["milestones"]:
                lines.append(
                    f"| **{m['project_name']}** | `{m['status']}` | {m['completed_tasks']}/{m['total_tasks']} | {m['blocked_tasks']} | **{m['completion_percentage']}%** |"
                )

        lines.extend([
            "",
            "## 👥 AI Workforce Health",
            f"- **Headcount**: {data['active_workforce']['total_employees']} employees ({data['active_workforce']['working']} actively working, {data['active_workforce']['idle']} idle)",
            f"- **Verified Skills**: {data['active_workforce']['skills']['total_verified']} / {data['active_workforce']['skills']['total_declared']} ({data['active_workforce']['skills']['verification_rate_pct']}% verified)",
            "",
            "## 🛑 Critical Blockers",
        ])

        if not data["blockers"]:
            lines.append("✅ **Zero active blockers.** Engineering execution progressing smoothly.")
        else:
            for b in data["blockers"]:
                lines.append(
                    f"- ⚠️ **{b['title']}** (`{b['task_id']}`) in project *{b['project_name']}* — Assigned to `{b['assigned_to']}` `[{b['status']}]`"
                )

        lines.extend([
            "",
            "## 💰 Financial Compute & Token Spend",
            f"- **Total Incurred Cost**: **${data['financial_costs']['total_usd']:.4f} USD**",
            f"- **Tokens Processed**: {data['financial_costs']['total_tokens']:,} tokens",
            "",
            "## 🔐 Pending Human Approvals",
        ])

        if not data["pending_approvals"]:
            lines.append("✅ **No pending approval requests.** Human sovereignty gates clear.")
        else:
            for app in data["pending_approvals"]:
                lines.append(
                    f"- 🔒 **{app['action_type']}** requested by `{app['requested_by']}`: {app['description']} *(Justification: {app['justification']})*"
                )

        lines.extend([
            "",
            "## 📋 Boardroom Action Items",
            f"- Open Action Items: **{data['action_items_summary']['open_count']}**",
            f"- Overdue Items: **{data['action_items_summary']['overdue_count']}**",
        ])

        return "\n".join(lines)
