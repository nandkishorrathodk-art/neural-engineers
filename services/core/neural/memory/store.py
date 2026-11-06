from __future__ import annotations

import uuid
from typing import Any, Sequence

from neural.cognitive_fabric.memory.models import (
    MemoryLayer,
    MemoryRecord,
    RecordVerificationStatus,
)
from neural.cognitive_fabric.memory.six_tier_store import SixTierMemoryStore
from neural.constants import MemoryCategory
from neural.db.models import MemoryEntry
from neural.db.repositories import MemoryRepository
from neural.logger import get_logger

logger = get_logger("ancos.memory.store")


class OrganizationalMemoryStore:
    """
    Unified Organizational Memory Store:
    Integrates 6 complementary memory layers:
    1. Working: Transient active context, turn state.
    2. Episodic: Execution history, task receipts, failure/success traces.
    3. Semantic: Architectural patterns, technical concepts, design rationale.
    4. Procedural: Verified workflows, step-by-step guides, operational procedures.
    5. Relational: Employees, projects, decisions, dependencies.
    6. Organizational: Immutable company policies, security rules, safety envelopes.
    """

    def __init__(self, memory_repo: MemoryRepository | None = None) -> None:
        self.memory_repo = memory_repo
        self._in_memory_store: list[MemoryEntry] = []
        self.six_tier_store = SixTierMemoryStore(memory_repo=None)

    async def store_entry(
        self,
        category: MemoryCategory | str,
        source: str,
        content: str,
        metadata_json: dict[str, Any] | None = None,
        confidence: float = 1.0,
        embedding_ref: str | None = None,
    ) -> MemoryEntry:
        cat_enum = category if isinstance(category, MemoryCategory) else MemoryCategory(category.lower())
        logger.info("Storing memory entry", category=cat_enum.value, source=source)

        entry_id = str(uuid.uuid4())
        meta = metadata_json or {}

        # Also store into 6-tier store
        cat_to_layer = {
            MemoryCategory.EPISODE: MemoryLayer.EPISODIC,
            MemoryCategory.KNOWLEDGE: MemoryLayer.SEMANTIC,
            MemoryCategory.PROCEDURE: MemoryLayer.PROCEDURAL,
            MemoryCategory.POLICY: MemoryLayer.ORGANIZATIONAL,
            MemoryCategory.DECISION: MemoryLayer.ORGANIZATIONAL,
            MemoryCategory.LESSON: MemoryLayer.EPISODIC,
        }
        layer = cat_to_layer.get(cat_enum, MemoryLayer.SEMANTIC)
        rec = MemoryRecord(
            id=entry_id,
            layer=layer,
            category=cat_enum.value,
            source=source,
            content=content,
            confidence=confidence,
            metadata=meta,
            # Confidence is a relevance estimate, not proof that an outcome was
            # independently verified. New organizational entries stay untrusted
            # until a receipt-bound verification path promotes them.
            verification_status=RecordVerificationStatus.UNVERIFIED,
        )
        await self.six_tier_store.store_record(rec)

        if self.memory_repo:
            persisted_metadata = {
                **meta,
                "layer": rec.layer.value,
                "category": rec.category,
                "access_level": rec.access_level.value,
                "owner_employee_id": rec.owner_employee_id,
                "allowed_departments": rec.allowed_departments,
                "allowed_projects": rec.allowed_projects,
                "version": rec.version,
                "provenance": rec.provenance,
                "record_metadata": rec.metadata,
                "record_schema_version": 1,
                "record_id": rec.id,
                "created_at": rec.created_at.isoformat(),
                "verified_at": rec.verified_at.isoformat() if rec.verified_at else None,
                "related_record_ids": rec.related_record_ids,
                "embedding": rec.embedding,
            }
            return await self.memory_repo.create_entry(
                category=cat_enum,
                source=source,
                content=content,
                metadata_json=persisted_metadata,
                confidence=confidence,
                embedding_ref=embedding_ref,
            )

        entry = MemoryEntry(
            id=entry_id,
            category=cat_enum,
            source=source,
            content=content,
            metadata_json=meta,
            confidence=confidence,
            embedding_ref=embedding_ref,
        )
        self._in_memory_store.append(entry)
        return entry

    async def store_episodic(
        self,
        task_id: str,
        employee_id: str,
        outcome: str,
        summary: str,
        receipt: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        return await self.store_entry(
            category=MemoryCategory.EPISODE,
            source=f"task:{task_id}",
            content=summary,
            metadata_json={
                "task_id": task_id,
                "employee_id": employee_id,
                "outcome": outcome,
                "receipt": receipt or {},
            },
        )

    async def store_semantic(
        self,
        concept: str,
        explanation: str,
        tags: list[str] | None = None,
    ) -> MemoryEntry:
        return await self.store_entry(
            category=MemoryCategory.KNOWLEDGE,
            source=f"concept:{concept}",
            content=f"{concept}: {explanation}",
            metadata_json={"concept": concept, "tags": tags or []},
        )

    async def store_procedural(
        self,
        procedure_name: str,
        steps: list[dict[str, Any]],
        method_id: str | None = None,
    ) -> MemoryEntry:
        return await self.store_entry(
            category=MemoryCategory.PROCEDURE,
            source=f"procedure:{procedure_name}",
            content=f"Standard Operating Procedure for {procedure_name}",
            metadata_json={"procedure_name": procedure_name, "steps": steps, "method_id": method_id},
        )

    async def store_policy(
        self,
        policy_name: str,
        statement: str,
        enforcement_level: str = "mandatory",
    ) -> MemoryEntry:
        return await self.store_entry(
            category=MemoryCategory.POLICY,
            source=f"policy:{policy_name}",
            content=statement,
            metadata_json={"policy_name": policy_name, "enforcement_level": enforcement_level},
        )

    async def query_memory(
        self,
        query: str | None = None,
        category: MemoryCategory | str | None = None,
        source: str | None = None,
        limit: int = 50,
    ) -> Sequence[MemoryEntry]:
        cat_enum = None
        if category:
            cat_enum = category if isinstance(category, MemoryCategory) else MemoryCategory(category.lower())

        if self.memory_repo:
            return await self.memory_repo.list_entries(
                category=cat_enum,
                source=source,
                search=query,
                limit=limit,
            )

        results = []
        q_lower = query.lower() if query else None
        for entry in self._in_memory_store:
            if cat_enum and entry.category != cat_enum:
                continue
            if source and entry.source != source:
                continue
            if q_lower and q_lower not in entry.content.lower():
                continue
            results.append(entry)

        return results[:limit]

    async def get_tier_stats(self) -> dict[str, int]:
        if self.memory_repo:
            return await self.memory_repo.count_by_category()

        counts = {cat.value: 0 for cat in MemoryCategory}
        for entry in self._in_memory_store:
            counts[entry.category.value] += 1
        return counts

