from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from neural.db.models import Proposal
from neural.governance.committee import CommitteeMember
from neural.logger import get_logger

logger = get_logger("ancos.governance.deliberation")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DeliberationEngine:
    """
    Consensus Deliberation Engine.
    Facilitates structured debate and reasoning across Department Heads / Committee
    using the LLM Gateway with deterministic fallback.
    """

    def __init__(
        self,
        llm_gateway: Any | None = None,
        message_bus: Any | None = None
    ):
        self.llm_gateway = llm_gateway
        self.message_bus = message_bus

    async def generate_department_stance(
        self,
        member: CommitteeMember,
        proposal: Proposal
    ) -> dict[str, Any]:
        """
        Generates a departmental perspective on a proposal.
        Attempts LLM reasoning first; falls back to deterministic domain heuristics.
        """
        # 1. Attempt LLM Gateway reasoning if available
        if self.llm_gateway:
            try:
                system_prompt = (
                    f"You are {member.name}, serving as {member.role.value} in the "
                    f"Boardroom Governance Committee for department '{member.department}'. "
                    "Analyze the given corporate proposal strictly through your departmental lens. "
                    "Respond with a JSON object: "
                    '{"stance": "support"|"oppose"|"neutral", "argument": "string", "key_concerns": ["str"]}'
                )
                user_prompt = (
                    f"Proposal ID: {proposal.proposal_id}\n"
                    f"Title: {proposal.title}\n"
                    f"Type: {proposal.proposal_type.value}\n"
                    f"Description: {proposal.description}\n"
                    f"Parameters: {json.dumps(proposal.parameters or {})}"
                )

                response = await self.llm_gateway.generate(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    tier="reasoning",
                    temperature=0.2,
                    max_tokens=600
                )
                from neural.llm.gateway import extract_json_from_text
                parsed = extract_json_from_text(response.content)
                if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                    parsed = parsed[0]
                elif not isinstance(parsed, dict):
                    parsed = {}

                return {
                    "speaker_id": member.member_id,
                    "speaker_name": member.name,
                    "role": member.role.value,
                    "department": member.department,
                    "stance": parsed.get("stance", "support"),
                    "argument": parsed.get("argument", f"Reviewed and aligned from {member.department} perspective."),
                    "key_concerns": parsed.get("key_concerns", []),
                    "timestamp": utc_now_iso(),
                    "source": "llm_reasoning",
                }
            except Exception as e:  # noqa: BLE001
                logger.debug("Deliberation fallback to heuristic", member=member.member_id, error=str(e))

        # 2. Deterministic Fallback Heuristics
        return self._generate_heuristic_stance(member, proposal)

    def _generate_heuristic_stance(
        self,
        member: CommitteeMember,
        proposal: Proposal
    ) -> dict[str, Any]:
        dept_lower = member.department.lower()
        title_lower = proposal.title.lower()
        desc_lower = proposal.description.lower()

        stance = "support"
        argument = f"{member.department} endorses the proposal with standard operational safeguards."
        concerns: list[str] = []

        if "security" in dept_lower:
            if "override" in title_lower or "emergency" in desc_lower:
                stance = "oppose"
                argument = "Security vetoes unauthorized privilege escalation without audit logs."
                concerns = ["Audit trail requirement", "Least-privilege isolation"]
            else:
                argument = "Security approves with strict boundary sandboxing and non-root container isolation."
                concerns = ["Network ingress restrictions", "Credential lifecycle rotation"]

        elif "qa" in dept_lower or "quality" in dept_lower:
            stance = "support"
            argument = "QA requires empirical test verification receipts before final status completion."
            concerns = ["Unit & integration test pass rate", "Zero-success-by-decree enforcement"]

        elif "infrastructure" in dept_lower or "devops" in dept_lower:
            stance = "support"
            argument = "Infrastructure confirms Docker workstation resources, VNC routing, and bridge isolation are ready."
            concerns = ["Memory ceiling caps", "Port collision prevention"]

        elif "engineering" in dept_lower:
            stance = "support"
            argument = "Engineering approves modular architecture, typed contracts, and DAG dependency graph resolution."
            concerns = ["Codebase maintainability", "Async deadlock prevention"]

        elif "product" in dept_lower:
            stance = "support"
            argument = "Product aligns with corporate milestone delivery and customer value requirements."
            concerns = ["Feature release cadence", "User experience parity"]

        elif "finance" in dept_lower:
            budget = (proposal.parameters or {}).get("budget_usd", 0.0)
            if budget > 50.0:
                stance = "neutral"
                argument = f"Finance requests cost monitoring for budget allocation of ${budget:.2f}."
                concerns = ["Token burn rate", "ROI justification"]
            else:
                argument = "Finance confirms current compute and model token burn are within operating budget."

        elif "executive" in dept_lower or member.member_id == "BossAI":
            argument = "Executive chair oversees consensus deliberation and ensures strategic alignment with enterprise mission."

        return {
            "speaker_id": member.member_id,
            "speaker_name": member.name,
            "role": member.role.value,
            "department": member.department,
            "stance": stance,
            "argument": argument,
            "key_concerns": concerns,
            "timestamp": utc_now_iso(),
            "source": "heuristic_governance",
        }

    async def run_deliberation_round(
        self,
        proposal: Proposal,
        members: Sequence[CommitteeMember]
    ) -> list[dict[str, Any]]:
        """
        Runs a comprehensive deliberation round across all provided committee members in parallel.
        Broadcasts debate messages to the Comms bus.
        """
        import asyncio

        tasks = [self.generate_department_stance(member=m, proposal=proposal) for m in members]
        statements = list(await asyncio.gather(*tasks))

        if self.message_bus:
            for m, statement in zip(members, statements):
                try:
                    await self.message_bus.publish(
                        channel="boardroom:deliberation",
                        message={
                            "proposal_id": proposal.proposal_id,
                            "speaker": m.name,
                            "role": m.role.value,
                            "stance": statement["stance"],
                            "argument": statement["argument"],
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug("Failed to publish deliberation to bus", error=str(e))

        return statements
