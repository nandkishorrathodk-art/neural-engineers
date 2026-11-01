from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from neural.db.models import DecisionLedgerEntry
from neural.db.repositories import DecisionLedgerRepository
from neural.logger import get_logger

logger = get_logger("ancos.governance.ledger")


class DecisionLedger:
    """
    Immutable, Hash-Chained Decision Ledger for Boardroom Governance.
    Guarantees cryptographic provenance and tamper-detection for all executive decisions.
    """

    def __init__(self, repo: DecisionLedgerRepository):
        self.repo = repo

    async def record_decision(
        self,
        proposal_id: str,
        decision: str,
        title: str,
        proposal_type: str,
        proposer_id: str,
        votes_summary: dict[str, Any],
        rationale: str,
        execution_receipt: dict[str, Any] | None = None
    ) -> DecisionLedgerEntry:
        """
        Appends an immutable entry to the ledger with SHA-256 hash chaining.
        """
        entry = await self.repo.append_entry(
            proposal_id=proposal_id,
            decision=decision,
            title=title,
            proposal_type=proposal_type,
            proposer_id=proposer_id,
            votes_summary=votes_summary,
            rationale=rationale,
            execution_receipt=execution_receipt
        )
        logger.info(
            "Decision ledger entry recorded",
            entry_id=entry.entry_id,
            sequence=entry.sequence_number,
            decision=decision,
            hash=entry.entry_hash[:16]
        )
        return entry

    async def get_entry(self, entry_id: str) -> DecisionLedgerEntry | None:
        return await self.repo.get_by_entry_id(entry_id)

    async def get_by_proposal(self, proposal_id: str) -> DecisionLedgerEntry | None:
        return await self.repo.get_by_proposal_id(proposal_id)

    async def list_entries(self, limit: int = 100) -> Sequence[DecisionLedgerEntry]:
        return await self.repo.list_all(limit=limit)

    async def verify_integrity(self) -> tuple[bool, str | None]:
        """
        Validates hash chaining and cryptographic integrity across all ledger records.
        """
        return await self.repo.verify_chain_integrity()
