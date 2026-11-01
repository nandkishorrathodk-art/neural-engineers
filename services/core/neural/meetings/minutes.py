import uuid
from datetime import UTC, datetime
from typing import Any

from neural.constants import MeetingType
from neural.db.models import ActionItem
from neural.logger import get_logger

logger = get_logger("ancos.meetings.minutes")


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MeetingMinutesGenerator:
    """
    Generates structured, auditable meeting minutes with explicit decisions,
    rationales, fact-grounded progress reports, and tracked action items.
    """

    @classmethod
    def generate_minutes(
        cls,
        meeting_id: str,
        title: str,
        meeting_type: MeetingType | str,
        participants: list[str],
        agenda: list[str],
        started_at: datetime | None = None,
        concluded_at: datetime | None = None,
        progress_reports: list[dict[str, Any]] | None = None,
        decisions: list[dict[str, Any]] | None = None,
        action_items: list[dict[str, Any] | ActionItem] | None = None,
        summary: str | None = None
    ) -> dict[str, Any]:
        """
        Compiles a comprehensive minutes dictionary with explicit decisions and rationales.
        """
        reports = progress_reports or []
        m_type_val = meeting_type.value if hasattr(meeting_type, "value") else str(meeting_type)

        # Extract blockers from progress reports
        blockers: list[dict[str, Any]] = []
        for r in reports:
            blocker = r.get("what_is_blocking") or r.get("blocker")
            if blocker and str(blocker).strip().lower() not in ("none", "n/a", "no", "false"):
                blockers.append({
                    "task_id": r.get("task_id", "UNKNOWN"),
                    "employee_id": r.get("employee_id", "UNKNOWN"),
                    "blocker": str(blocker).strip()
                })

        # Format decisions
        formatted_decisions: list[dict[str, Any]] = []
        for d in (decisions or []):
            d_id = d.get("decision_id") or f"DEC-{uuid.uuid4().hex[:6].upper()}"
            formatted_decisions.append({
                "decision_id": d_id,
                "topic": d.get("topic", "General"),
                "decision": d.get("decision", ""),
                "rationale": d.get("rationale", "Autonomous operational consensus"),
                "decided_by": d.get("decided_by", "BossAI"),
                "impact": d.get("impact", "Standard workflow")
            })

        # Format action items
        formatted_action_items: list[dict[str, Any]] = []
        for ai in (action_items or []):
            if isinstance(ai, ActionItem):
                formatted_action_items.append({
                    "id": ai.id,
                    "description": ai.description,
                    "assigned_to": ai.assigned_to,
                    "deadline": ai.deadline.isoformat() if ai.deadline else None,
                    "status": ai.status.value if hasattr(ai.status, "value") else str(ai.status)
                })
            elif isinstance(ai, dict):
                formatted_action_items.append({
                    "id": ai.get("id", str(uuid.uuid4())),
                    "description": ai.get("description", ""),
                    "assigned_to": ai.get("assigned_to", "BossAI"),
                    "deadline": ai.get("deadline"),
                    "status": ai.get("status", "open")
                })

        default_summary = (
            f"Autonomous {m_type_val.replace('_', ' ').title()} concluded. "
            f"{len(reports)} progress report(s) evaluated, {len(blockers)} blocker(s) identified, "
            f"{len(formatted_decisions)} strategic decision(s) ratified, and "
            f"{len(formatted_action_items)} action item(s) assigned."
        )

        minutes = {
            "meeting_id": meeting_id,
            "title": title,
            "meeting_type": m_type_val,
            "started_at": started_at.isoformat() if started_at else None,
            "concluded_at": (concluded_at or utc_now()).isoformat(),
            "participants": participants,
            "agenda": agenda,
            "summary": summary or default_summary,
            "progress_reports": reports,
            "blockers_identified": blockers,
            "decisions": formatted_decisions,
            "action_items": formatted_action_items,
            "total_reports": len(reports),
            "total_blockers": len(blockers),
            "total_decisions": len(formatted_decisions),
            "total_action_items": len(formatted_action_items)
        }

        logger.info(
            "Generated meeting minutes",
            meeting_id=meeting_id,
            decisions_count=len(formatted_decisions),
            action_items_count=len(formatted_action_items)
        )
        return minutes

    @classmethod
    def format_as_markdown(cls, minutes: dict[str, Any]) -> str:
        """
        Renders structured minutes dictionary into clean GitHub-style Markdown.
        """
        lines = [
            f"# 🏛️ Meeting Minutes: {minutes.get('title', 'Autonomous Meeting')}",
            f"**Meeting ID**: `{minutes.get('meeting_id')}` | **Type**: `{minutes.get('meeting_type')}`",
            f"**Concluded At**: {minutes.get('concluded_at', 'N/A')}",
            "",
            "## 👥 Attendees",
            ", ".join(f"`{p}`" for p in minutes.get("participants", [])),
            "",
            "## 📋 Agenda",
        ]
        for item in minutes.get("agenda", []):
            lines.append(f"- [x] {item}")

        lines.extend([
            "",
            "## 📝 Executive Summary",
            minutes.get("summary", ""),
            "",
            "## 📊 Fact-Grounded Progress Reports",
        ])

        reports = minutes.get("progress_reports", [])
        if not reports:
            lines.append("_No progress reports recorded for this session._")
        else:
            for idx, r in enumerate(reports, 1):
                lines.extend([
                    f"### Report {idx}: {r.get('employee_id', 'Unknown')} (Task: `{r.get('task_id', 'N/A')}`)",
                    f"- **Done**: {r.get('what_was_done', 'N/A')}",
                    f"- **Remains**: {r.get('what_remains', 'N/A')}",
                    f"- **Blocker**: {r.get('what_is_blocking') or 'None'}",
                    f"- **Receipt**: `{r.get('verification_receipt', {})}`",
                    ""
                ])

        lines.append("## ⚖️ Strategic Decisions & Rationales")
        decisions = minutes.get("decisions", [])
        if not decisions:
            lines.append("_No formal decisions recorded._")
        else:
            for d in decisions:
                lines.extend([
                    f"### 🔹 {d.get('topic', 'General Decision')} (`{d.get('decision_id')}`)",
                    f"- **Decision**: {d.get('decision')}",
                    f"- **Rationale**: {d.get('rationale')}",
                    f"- **Decided By**: `{d.get('decided_by')}`",
                    f"- **Impact**: {d.get('impact', 'Standard')}",
                    ""
                ])

        lines.append("## 📌 Action Items")
        action_items = minutes.get("action_items", [])
        if not action_items:
            lines.append("_No new or carried-over action items._")
        else:
            for ai in action_items:
                deadline = f" (Due: {ai.get('deadline')})" if ai.get("deadline") else ""
                lines.append(
                    f"- [ ] **{ai.get('description')}** — Assigned to `{ai.get('assigned_to')}`{deadline} `[{ai.get('status')}]`"
                )

        return "\n".join(lines)
