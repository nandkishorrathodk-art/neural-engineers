from __future__ import annotations

import hashlib
from typing import Sequence

from neural.db.models import AuditLog
from neural.exceptions import AuditIntegrityError
from neural.logger import get_logger

logger = get_logger("ancos.observability.audit")


class AuditLogChainVerifier:
    """
    Verifies cryptographic SHA-256 tamper-evident integrity across AuditLog records.
    Validates that:
    1. The genesis block links to 'GENESIS_AUDIT_BLOCK'.
    2. Every subsequent block links to the preceding block's signature via `previous_hash`.
    3. Every block's signature matches the re-computed SHA-256 hash of its payload, action, actor, and previous_hash.
    """

    @classmethod
    def compute_signature(cls, record: AuditLog) -> str:
        sig_raw = (
            f"{record.previous_hash}:{record.actor}:{record.action}:"
            f"{record.resource_type}:{record.resource_id}:{record.request_hash}"
        )
        return hashlib.sha256(sig_raw.encode("utf-8")).hexdigest()

    @classmethod
    def order_chain(cls, records: Sequence[AuditLog]) -> list[AuditLog]:
        """
        Orders audit records by traversing the cryptographic chain from GENESIS_AUDIT_BLOCK.
        Guarantees deterministic ordering immune to timestamp collisions or database sort order.
        """
        if not records:
            return []

        by_prev: dict[str, AuditLog] = {}
        for r in records:
            if r.previous_hash:
                by_prev[r.previous_hash] = r

        ordered: list[AuditLog] = []
        curr_prev = "GENESIS_AUDIT_BLOCK"
        visited_ids: set[str] = set()

        while curr_prev in by_prev:
            rec = by_prev[curr_prev]
            if rec.id in visited_ids:
                break
            visited_ids.add(rec.id)
            ordered.append(rec)
            curr_prev = rec.signature or ""

        # If any records remain (e.g. orphans or alternative forks), append deterministically
        remaining = [r for r in records if r.id not in visited_ids]
        if remaining:
            remaining.sort(key=lambda r: (r.timestamp.isoformat() if r.timestamp else "", r.id))
            ordered.extend(remaining)

        return ordered

    @classmethod
    def verify_records(cls, records: Sequence[AuditLog]) -> tuple[bool, list[str]]:
        """
        Validates an ordered sequence of AuditLog records.
        Returns (is_valid: bool, issues: list[str]).
        """
        if not records:
            return True, []

        issues: list[str] = []
        expected_prev = "GENESIS_AUDIT_BLOCK"

        for idx, rec in enumerate(records):
            if idx == 0:
                if rec.previous_hash != "GENESIS_AUDIT_BLOCK":
                    issues.append(
                        f"Genesis block link violated at id={rec.id}: "
                        f"expected 'GENESIS_AUDIT_BLOCK', got '{rec.previous_hash}'"
                    )
            else:
                if rec.previous_hash != expected_prev:
                    issues.append(
                        f"Chain broken at record id={rec.id} (index {idx}): "
                        f"expected previous_hash '{expected_prev}', got '{rec.previous_hash}'"
                    )

            computed_sig = cls.compute_signature(rec)
            if rec.signature and rec.signature != computed_sig:
                issues.append(
                    f"Signature mismatch at record id={rec.id}: "
                    f"expected '{computed_sig}', got '{rec.signature}'"
                )

            expected_prev = rec.signature or ""

        is_valid = len(issues) == 0
        if not is_valid:
            logger.error("Audit log tamper-evident verification failed", issues=issues)
        return is_valid, issues

    @classmethod
    def enforce_integrity(cls, records: Sequence[AuditLog]) -> None:
        """Raises AuditIntegrityError if any chain verification failure is detected."""
        valid, issues = cls.verify_records(records)
        if not valid:
            raise AuditIntegrityError(f"Audit log chain integrity violated: {'; '.join(issues)}")
