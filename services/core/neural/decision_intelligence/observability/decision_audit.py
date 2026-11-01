from __future__ import annotations

import hashlib
import threading
import time
from typing import Optional

from pydantic import BaseModel, Field

from neural.exceptions import AuditIntegrityError
from neural.logger import get_logger

logger = get_logger("ancos.decision_intelligence.observability.decision_audit")

GENESIS_HASH: str = "0" * 64


class AuditEntry(BaseModel):
    entry_id: int
    decision_id: str
    decision_type: str
    output_choice: Optional[str]
    confidence: float
    timestamp: float = Field(default_factory=time.time)
    prev_hash: str
    entry_hash: str


class DecisionAuditLogger:
    """
    Tamper-Evident Audit Logging for System 1 Decisions (Section 13).
    Maintains a cryptographic SHA-256 hash chain of all cognitive choices.
    """

    def __init__(self) -> None:
        self._chain: list[AuditEntry] = []
        self._last_hash = GENESIS_HASH
        self._lock = threading.Lock()

    def log_decision(
        self,
        decision_id: str,
        decision_type: str,
        output_choice: Optional[str],
        confidence: float,
    ) -> AuditEntry:
        with self._lock:
            entry_id = len(self._chain) + 1
            now = time.time()
            prev_h = self._last_hash

            # Compute tamper-evident hash
            payload = f"{entry_id}:{decision_id}:{decision_type}:{output_choice}:{confidence:.4f}:{now:.4f}:{prev_h}"
            curr_h = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            entry = AuditEntry(
                entry_id=entry_id,
                decision_id=decision_id,
                decision_type=decision_type,
                output_choice=output_choice,
                confidence=confidence,
                timestamp=now,
                prev_hash=prev_h,
                entry_hash=curr_h,
            )

            self._chain.append(entry)
            self._last_hash = curr_h
            return entry

    def verify_integrity(self, raise_on_failure: bool = False) -> tuple[bool, Optional[int]]:
        with self._lock:
            last_h = GENESIS_HASH
            for idx, entry in enumerate(self._chain):
                if entry.prev_hash != last_h:
                    logger.error("Audit chain broken: prev_hash mismatch", index=idx, expected=last_h, got=entry.prev_hash)
                    if raise_on_failure:
                        raise AuditIntegrityError(f"Audit log tampered at index {idx}: prev_hash mismatch")
                    return False, idx

                payload = (
                    f"{entry.entry_id}:{entry.decision_id}:{entry.decision_type}:"
                    f"{entry.output_choice}:{entry.confidence:.4f}:{entry.timestamp:.4f}:{entry.prev_hash}"
                )
                expected_h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                if entry.entry_hash != expected_h:
                    logger.error("Audit chain broken: entry_hash mismatch", index=idx)
                    if raise_on_failure:
                        raise AuditIntegrityError(f"Audit log tampered at index {idx}: signature mismatch")
                    return False, idx

                last_h = entry.entry_hash

            return True, None

    def get_entries(self, limit: int = 100) -> list[AuditEntry]:
        return self._chain[-limit:]


_audit_logger_instance: Optional[DecisionAuditLogger] = None


def get_decision_audit_logger() -> DecisionAuditLogger:
    global _audit_logger_instance
    if _audit_logger_instance is None:
        _audit_logger_instance = DecisionAuditLogger()
    return _audit_logger_instance


def reset_decision_audit_logger() -> None:
    global _audit_logger_instance
    _audit_logger_instance = None
