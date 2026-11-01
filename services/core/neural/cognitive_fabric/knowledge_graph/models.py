from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EntityType(str, Enum):
    COMPANY = "company"
    DEPARTMENT = "department"
    EMPLOYEE = "employee"
    SKILL = "skill"
    PROJECT = "project"
    TASK = "task"
    DEPENDENCY = "dependency"
    MEETING = "meeting"
    DECISION = "decision"
    ARTIFACT = "artifact"
    FAILURE = "failure"
    EXPERIMENT = "experiment"
    LEARNING_EXPERIENCE = "learning_experience"
    PROCEDURE = "procedure"
    MODEL = "model"


class RelationType(str, Enum):
    BELONGS_TO_DEPARTMENT = "belongs_to_department"
    HAS_SKILL = "has_skill"
    COMPLETED_TASK = "completed_task"
    ASSIGNED_TO = "assigned_to"
    CONTRIBUTED_TO_PROJECT = "contributed_to_project"
    PRODUCED_ARTIFACT = "produced_artifact"
    VERIFIED_BY = "verified_by"
    ATTENDED_MEETING = "attended_meeting"
    DECIDED_IN_MEETING = "decided_in_meeting"
    AFFECTS_PROJECT = "affects_project"
    DEPENDS_ON = "depends_on"
    CAUSED_FAILURE = "caused_failure"
    RESOLVED_BY = "resolved_by"
    SUPERSEDED_BY = "superseded_by"
    USES_MODEL = "uses_model"
    FOLLOWS_PROCEDURE = "follows_procedure"


@dataclass
class GraphEntity:
    entity_id: str
    entity_type: EntityType
    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    source: str = "ancos_core"
    verification_status: str = "verified"
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "name": self.name,
            "properties": self.properties,
            "source": self.source,
            "verification_status": self.verification_status,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class TemporalRelationship:
    rel_id: str
    from_entity_id: str
    to_entity_id: str
    rel_type: RelationType
    source: str
    valid_from: datetime = field(default_factory=utc_now)
    valid_to: Optional[datetime] = None
    verification_status: str = "unverified"  # verified, unverified, disputed, superseded
    confidence: float = 1.0
    permissions: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.valid_to is None and self.verification_status != "superseded"

    def is_valid_at(self, timestamp: datetime) -> bool:
        if self.valid_from > timestamp:
            return False
        if self.valid_to is not None and self.valid_to <= timestamp:
            return False
        return True

    def mark_superseded(self, superseded_by_rel_id: Optional[str] = None) -> None:
        self.verification_status = "superseded"
        if self.valid_to is None:
            self.valid_to = utc_now()
        if superseded_by_rel_id:
            self.properties["superseded_by"] = superseded_by_rel_id

    def mark_disputed(self, reason: str, disputer: str) -> None:
        self.verification_status = "disputed"
        self.properties["dispute"] = {
            "reason": reason,
            "disputer": disputer,
            "timestamp": utc_now().isoformat(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "rel_id": self.rel_id,
            "from_entity_id": self.from_entity_id,
            "to_entity_id": self.to_entity_id,
            "rel_type": self.rel_type.value,
            "source": self.source,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "verification_status": self.verification_status,
            "confidence": self.confidence,
            "permissions": self.permissions,
            "properties": self.properties,
            "is_active": self.is_active,
        }

