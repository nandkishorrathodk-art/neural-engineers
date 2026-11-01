from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import networkx as nx

from neural.cognitive_fabric.knowledge_graph.models import (
    EntityType,
    GraphEntity,
    RelationType,
    TemporalRelationship,
    utc_now,
)
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.knowledge_graph.temporal")


class TemporalKnowledgeGraph:
    """
    First-Class Temporal Organizational Knowledge Graph.
    Tracks entities, temporal relationships, validity periods, disputes,
    superseded decisions, and historical graph states.
    Completely separate from the Task execution DAG.
    """

    def __init__(self) -> None:
        self._entities: dict[str, GraphEntity] = {}
        self._relationships: dict[str, TemporalRelationship] = {}
        self._nx_graph = nx.MultiDiGraph()

    def add_entity(
        self,
        entity_id: str,
        entity_type: EntityType | str,
        name: str,
        properties: Optional[dict[str, Any]] = None,
        source: str = "ancos_core",
        verification_status: str = "verified",
    ) -> GraphEntity:
        e_type = entity_type if isinstance(entity_type, EntityType) else EntityType(entity_type.lower())
        entity = GraphEntity(
            entity_id=entity_id,
            entity_type=e_type,
            name=name,
            properties=properties or {},
            source=source,
            verification_status=verification_status,
        )
        self._entities[entity_id] = entity
        self._nx_graph.add_node(entity_id, **entity.to_dict())
        return entity

    def get_entity(self, entity_id: str) -> Optional[GraphEntity]:
        return self._entities.get(entity_id)

    def add_relationship(
        self,
        from_entity_id: str,
        to_entity_id: str,
        rel_type: RelationType | str,
        source: str,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        verification_status: str = "unverified",
        confidence: float = 1.0,
        properties: Optional[dict[str, Any]] = None,
        permissions: Optional[dict[str, Any]] = None,
    ) -> TemporalRelationship:
        r_type = rel_type if isinstance(rel_type, RelationType) else RelationType(rel_type.lower())
        rel_id = f"rel_{uuid.uuid4().hex[:8]}"
        vf = valid_from or utc_now()

        # Check for conflicting active relationship of the exact same type
        for existing in list(self._relationships.values()):
            if (
                existing.from_entity_id == from_entity_id
                and existing.to_entity_id == to_entity_id
                and existing.rel_type == r_type
                and existing.is_active
            ):
                # Detected potential conflict or skill/confidence update
                props = properties or {}
                if (
                    existing.properties != props
                    or existing.confidence != confidence
                    or existing.source != source
                    or existing.verification_status != verification_status
                ):
                    logger.info(
                        "Conflicting or updated relationship detected; marking existing as superseded",
                        old_rel_id=existing.rel_id,
                        new_rel_id=rel_id,
                    )
                    existing.mark_superseded(superseded_by_rel_id=rel_id)
                    if self._nx_graph.has_edge(existing.from_entity_id, existing.to_entity_id, key=existing.rel_id):
                        self._nx_graph[existing.from_entity_id][existing.to_entity_id][existing.rel_id]["verification_status"] = "superseded"
                        self._nx_graph[existing.from_entity_id][existing.to_entity_id][existing.rel_id]["valid_to"] = existing.valid_to.isoformat() if existing.valid_to else None

        rel = TemporalRelationship(
            rel_id=rel_id,
            from_entity_id=from_entity_id,
            to_entity_id=to_entity_id,
            rel_type=r_type,
            source=source,
            valid_from=vf,
            valid_to=valid_to,
            verification_status=verification_status,
            confidence=confidence,
            permissions=permissions or {},
            properties=properties or {},
        )
        self._relationships[rel_id] = rel
        self._nx_graph.add_edge(from_entity_id, to_entity_id, key=rel_id, **rel.to_dict())
        return rel

    def dispute_relationship(
        self,
        rel_id: str,
        reason: str,
        disputer: str,
        disputer_dept: Optional[str] = None,
    ) -> bool:
        rel = self._relationships.get(rel_id)
        if not rel:
            return False
        rel.mark_disputed(reason, disputer)
        if disputer_dept:
            rel.properties["dispute"]["disputer_dept"] = disputer_dept
        # Update NetworkX edge data
        if self._nx_graph.has_edge(rel.from_entity_id, rel.to_entity_id, key=rel_id):
            self._nx_graph[rel.from_entity_id][rel.to_entity_id][rel_id]["verification_status"] = "disputed"
            self._nx_graph[rel.from_entity_id][rel.to_entity_id][rel_id]["properties"]["dispute"] = rel.properties.get("dispute")
        return True

    def supersede_decision(self, old_decision_id: str, new_decision_id: str, source: str) -> bool:
        old_ent = self.get_entity(old_decision_id)
        new_ent = self.get_entity(new_decision_id)
        if not old_ent or not new_ent:
            return False

        # Mark all active relationships from old decision as superseded
        for rel in self._relationships.values():
            if rel.from_entity_id == old_decision_id and rel.is_active:
                rel.mark_superseded(new_decision_id)
                if self._nx_graph.has_edge(rel.from_entity_id, rel.to_entity_id, key=rel.rel_id):
                    self._nx_graph[rel.from_entity_id][rel.to_entity_id][rel.rel_id]["verification_status"] = "superseded"
                    self._nx_graph[rel.from_entity_id][rel.to_entity_id][rel.rel_id]["valid_to"] = rel.valid_to.isoformat() if rel.valid_to else None

        # Add SUPERSEDED_BY relationship
        self.add_relationship(
            from_entity_id=old_decision_id,
            to_entity_id=new_decision_id,
            rel_type=RelationType.SUPERSEDED_BY,
            source=source,
            verification_status="verified",
            confidence=1.0,
            properties={"superseded_at": utc_now().isoformat()},
        )
        return True

    def query_as_of(self, timestamp: datetime) -> list[TemporalRelationship]:
        """
        Returns all relationships that were active and valid at 'timestamp'.
        """
        return [r for r in self._relationships.values() if r.is_valid_at(timestamp)]

    def get_entity_history(self, entity_id: str) -> list[dict[str, Any]]:
        """
        Returns chronological timeline of an entity's relationships.
        """
        entity_rels = [
            r for r in self._relationships.values()
            if r.from_entity_id == entity_id or r.to_entity_id == entity_id
        ]
        entity_rels.sort(key=lambda r: r.valid_from)
        return [r.to_dict() for r in entity_rels]

    def count_entities(self) -> int:
        return len(self._entities)

    def count_relationships(self) -> int:
        return len(self._relationships)

