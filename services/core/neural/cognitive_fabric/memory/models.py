from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MemoryLayer(str, Enum):
    """
    Six complementary organizational memory layers:
    1. Working: Current objectives, task context, scratchpad, active turn state.
    2. Episodic: Completed projects, execution traces, receipts, failure patterns.
    3. Semantic: Verified company facts, technical concepts, architecture designs.
    4. Procedural: Reusable skills, standard workflows, certified operational recipes.
    5. Relational: Employees, projects, decisions, dependencies, team relationships.
    6. Organizational: Policies, strategies, boardroom governance, corporate memory.
    """
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    RELATIONAL = "relational"
    ORGANIZATIONAL = "organizational"


class MemoryAccessLevel(str, Enum):
    PUBLIC = "public"            # Readable by all employees & Boss
    INTERNAL = "internal"        # Default company internal
    CONFIDENTIAL = "confidential"# Department or project restricted
    RESTRICTED = "restricted"    # Employee private or Executive/Boss only


class RecordVerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"


@dataclass
class MemoryRecord:
    """
    Authoritative memory record schema with comprehensive provenance,
    temporal verification, versioning, and access control.
    """
    layer: MemoryLayer
    category: str
    source: str
    content: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=utc_now)
    verified_at: Optional[datetime] = None
    confidence: float = 1.0
    verification_status: RecordVerificationStatus = RecordVerificationStatus.UNVERIFIED
    access_level: MemoryAccessLevel = MemoryAccessLevel.INTERNAL
    owner_employee_id: Optional[str] = None
    allowed_departments: list[str] = field(default_factory=list)
    allowed_projects: list[str] = field(default_factory=list)
    version: int = 1
    related_record_ids: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: Optional[list[float]] = None

    @property
    def is_verified(self) -> bool:
        return (
            self.verification_status == RecordVerificationStatus.VERIFIED
            and self.confidence >= 0.75
            and self.verified_at is not None
        )

    def mark_verified(self, verifier_id: str, confidence: float = 1.0, notes: Optional[str] = None) -> None:
        self.verification_status = RecordVerificationStatus.VERIFIED
        self.verified_at = utc_now()
        self.confidence = max(0.0, min(1.0, confidence))
        self.provenance["verifier_id"] = verifier_id
        if notes:
            self.provenance["verification_notes"] = notes

    def mark_disputed(self, reason: str, disputer_id: str) -> None:
        self.verification_status = RecordVerificationStatus.DISPUTED
        self.provenance["dispute"] = {
            "disputer_id": disputer_id,
            "reason": reason,
            "disputed_at": utc_now().isoformat(),
        }

    def mark_superseded(self, superseded_by_id: str) -> None:
        self.verification_status = RecordVerificationStatus.SUPERSEDED
        self.provenance["superseded_by"] = superseded_by_id
        self.provenance["superseded_at"] = utc_now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "layer": self.layer.value,
            "category": self.category,
            "source": self.source,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "confidence": self.confidence,
            "verification_status": self.verification_status.value,
            "access_level": self.access_level.value,
            "owner_employee_id": self.owner_employee_id,
            "allowed_departments": self.allowed_departments,
            "allowed_projects": self.allowed_projects,
            "version": self.version,
            "related_record_ids": self.related_record_ids,
            "provenance": self.provenance,
            "metadata": self.metadata,
            "is_verified": self.is_verified,
        }

