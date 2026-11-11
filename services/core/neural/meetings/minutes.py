import hashlib
import json
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
        summary: str | None = None,
        verified_claims: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Compiles a comprehensive minutes dictionary with explicit decisions and rationales.
        """
        reports = progress_reports or []
        claims = verified_claims or []
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
                    "status": ai.status.value if hasattr(ai.status, "value") else str(ai.status),
                    "linked_task_id": getattr(ai, "linked_task_id", None),
                    "acceptance_criteria": getattr(ai, "acceptance_criteria", None),
                })
            elif isinstance(ai, dict):
                formatted_action_items.append({
                    "id": ai.get("id", str(uuid.uuid4())),
                    "description": ai.get("description", ""),
                    "assigned_to": ai.get("assigned_to", "BossAI"),
                    "deadline": ai.get("deadline"),
                    "status": ai.get("status", "open"),
                    "linked_task_id": ai.get("linked_task_id"),
                    "acceptance_criteria": ai.get("acceptance_criteria"),
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
            "verified_claims": claims,
            "total_reports": len(reports),
            "total_blockers": len(blockers),
            "total_decisions": len(formatted_decisions),
            "total_action_items": len(formatted_action_items),
            "total_verified_claims": len(claims),
        }

        # Compute immutable SHA-256 minutes hash
        hashable_data = {
            "meeting_id": minutes["meeting_id"],
            "title": minutes["title"],
            "meeting_type": minutes["meeting_type"],
            "started_at": minutes["started_at"],
            "concluded_at": minutes["concluded_at"],
            "participants": sorted(minutes["participants"]),
            "agenda": minutes["agenda"],
            "summary": minutes["summary"],
            "progress_reports": minutes["progress_reports"],
            "verified_claims": minutes["verified_claims"],
            "decisions": minutes["decisions"],
            "action_items": minutes["action_items"],
            "blockers_identified": minutes["blockers_identified"],
        }
        canonical = json.dumps(hashable_data, sort_keys=True, default=str)
        minutes["minutes_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        logger.info(
            "Generated meeting minutes",
            meeting_id=meeting_id,
            decisions_count=len(formatted_decisions),
            action_items_count=len(formatted_action_items),
            minutes_hash=minutes["minutes_hash"][:16],
        )
        return minutes

    @classmethod
    def verify_minutes_integrity(cls, minutes: dict[str, Any]) -> tuple[bool, str | None]:
        """
        Cryptographically validates that meeting minutes have not been tampered with.
        """
        expected_hash = minutes.get("minutes_hash")
        if not expected_hash:
            return False, "Missing minutes_hash"
        hashable_data = {
            "meeting_id": minutes.get("meeting_id"),
            "title": minutes.get("title"),
            "meeting_type": minutes.get("meeting_type"),
            "started_at": minutes.get("started_at"),
            "concluded_at": minutes.get("concluded_at"),
            "participants": sorted(minutes.get("participants", [])),
            "agenda": minutes.get("agenda", []),
            "summary": minutes.get("summary", ""),
            "progress_reports": minutes.get("progress_reports", []),
            "verified_claims": minutes.get("verified_claims", []),
            "decisions": minutes.get("decisions", []),
            "action_items": minutes.get("action_items", []),
            "blockers_identified": minutes.get("blockers_identified", []),
        }
        canonical = json.dumps(hashable_data, sort_keys=True, default=str)
        actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            return False, f"Hash mismatch: expected {expected_hash}, calculated {actual_hash}"
        return True, None

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
