from __future__ import annotations

from typing import Any, Optional, Sequence

from neural.cognitive_fabric.memory.models import (
    MemoryAccessLevel,
    MemoryLayer,
    MemoryRecord,
    RecordVerificationStatus,
    utc_now,
)
from neural.cognitive_fabric.memory.permissions import MemoryAccessController
from neural.logger import get_logger

logger = get_logger("ancos.cognitive_fabric.memory.six_tier_store")


class SixTierMemoryStore:
    """
    Unified Six-Layer Organizational Memory Store:
    1. Working: Transient active context, current turn state, scratchpad.
    2. Episodic: Execution traces, milestone receipts, failure/success records.
    3. Semantic: Architecture patterns, technical facts, verified concepts.
    4. Procedural: Operational workflows, tested recipes, certified procedures.
    5. Relational: Inter-entity relationships, roles, dependencies, team graphs.
    6. Organizational: Policies, strategic directives, boardroom governance.
    """

    def __init__(self, memory_repo: Optional[Any] = None) -> None:
        self.memory_repo = memory_repo
        self._records: dict[str, MemoryRecord] = {}
        self.access_controller = MemoryAccessController()

    async def store_record(self, record: MemoryRecord) -> MemoryRecord:
        self._records[record.id] = record
        logger.info(
            "Stored 6-layer memory record",
            record_id=record.id,
            layer=record.layer.value,
            category=record.category,
            source=record.source,
            is_verified=record.is_verified,
        )

        # Write-through to existing relational memory repository if available
        if self.memory_repo:
            try:
                from neural.constants import MemoryCategory
                # Map 6 layers to backwards-compatible MemoryCategory
                cat_map = {
                    MemoryLayer.WORKING: MemoryCategory.EPISODE,
                    MemoryLayer.EPISODIC: MemoryCategory.EPISODE,
                    MemoryLayer.SEMANTIC: MemoryCategory.KNOWLEDGE,
                    MemoryLayer.PROCEDURAL: MemoryCategory.PROCEDURE,
                    MemoryLayer.RELATIONAL: MemoryCategory.KNOWLEDGE,
                    MemoryLayer.ORGANIZATIONAL: MemoryCategory.POLICY,
                }
                meta = dict(record.metadata)
                meta["layer"] = record.layer.value
                meta["access_level"] = record.access_level.value
                meta["verification_status"] = record.verification_status.value
                meta["owner_employee_id"] = record.owner_employee_id
                meta["allowed_departments"] = record.allowed_departments
                meta["allowed_projects"] = record.allowed_projects
                meta["version"] = record.version
                meta["provenance"] = record.provenance

                await self.memory_repo.create_entry(
                    category=cat_map.get(record.layer, MemoryCategory.KNOWLEDGE),
                    source=record.source,
                    content=record.content,
                    metadata_json=meta,
                    confidence=record.confidence,
                )
            except Exception as e:
                logger.warning("DB write-through skipped or failed", error=str(e))

        return record

    async def store_working(
        self,
        task_id: str,
        employee_id: str,
        content: str,
        scratchpad: Optional[dict[str, Any]] = None,
    ) -> MemoryRecord:
        record = MemoryRecord(
            layer=MemoryLayer.WORKING,
            category="scratchpad",
            source=f"task:{task_id}:working",
            content=content,
            owner_employee_id=employee_id,
            access_level=MemoryAccessLevel.RESTRICTED,
            verification_status=RecordVerificationStatus.UNVERIFIED,
            metadata={"scratchpad": scratchpad or {}, "task_id": task_id},
            provenance={"created_by": employee_id, "task_id": task_id},
        )
        return await self.store_record(record)

    async def store_episodic(
        self,
        task_id: str,
        employee_id: str,
        outcome: str,
        summary: str,
        receipt: Optional[dict[str, Any]] = None,
        confidence: float = 1.0,
        is_verified: bool = True,
    ) -> MemoryRecord:
        ver_status = RecordVerificationStatus.VERIFIED if is_verified else RecordVerificationStatus.UNVERIFIED
        record = MemoryRecord(
            layer=MemoryLayer.EPISODIC,
            category="task_receipt",
            source=f"task:{task_id}",
            content=summary,
            confidence=confidence,
            verification_status=ver_status,
            verified_at=utc_now() if is_verified else None,
            access_level=MemoryAccessLevel.INTERNAL,
            owner_employee_id=employee_id,
            metadata={"outcome": outcome, "task_id": task_id, "receipt": receipt or {}},
            provenance={"employee_id": employee_id, "outcome": outcome},
        )
        return await self.store_record(record)

    async def store_semantic(
        self,
        concept: str,
        explanation: str,
        source: str = "engineering_docs",
        tags: Optional[list[str]] = None,
        verified_by: Optional[str] = None,
        confidence: float = 1.0,
        access_level: MemoryAccessLevel = MemoryAccessLevel.INTERNAL,
        allowed_departments: Optional[list[str]] = None,
        allowed_projects: Optional[list[str]] = None,
    ) -> MemoryRecord:
        is_v = bool(verified_by is not None)
        record = MemoryRecord(
            layer=MemoryLayer.SEMANTIC,
            category="technical_fact",
            source=source,
            content=f"{concept}: {explanation}",
            confidence=confidence,
            verification_status=RecordVerificationStatus.VERIFIED if is_v else RecordVerificationStatus.UNVERIFIED,
            verified_at=utc_now() if is_v else None,
            access_level=access_level,
            allowed_departments=allowed_departments or [],
            allowed_projects=allowed_projects or [],
            metadata={"concept": concept, "tags": tags or []},
            provenance={"author": verified_by or "system", "verified_by": verified_by},
        )
        return await self.store_record(record)

    async def store_procedural(
        self,
        procedure_name: str,
        steps: list[dict[str, Any]],
        code_recipe: Optional[str] = None,
        verified_by: Optional[str] = None,
    ) -> MemoryRecord:
        is_v = bool(verified_by is not None)
        record = MemoryRecord(
            layer=MemoryLayer.PROCEDURAL,
            category="reusable_procedure",
            source=f"procedure:{procedure_name}",
            content=f"Standard Operating Procedure for {procedure_name}\nSteps: {len(steps)}",
            confidence=1.0 if is_v else 0.8,
            verification_status=RecordVerificationStatus.VERIFIED if is_v else RecordVerificationStatus.UNVERIFIED,
            verified_at=utc_now() if is_v else None,
            access_level=MemoryAccessLevel.INTERNAL,
            metadata={"procedure_name": procedure_name, "steps": steps, "code_recipe": code_recipe},
            provenance={"certified_by": verified_by},
        )
        return await self.store_record(record)

    async def store_relational(
        self,
        subject: str,
        predicate: str,
        target: str,
        context: Optional[dict[str, Any]] = None,
        verified_by: Optional[str] = None,
    ) -> MemoryRecord:
        is_v = bool(verified_by is not None)
        record = MemoryRecord(
            layer=MemoryLayer.RELATIONAL,
            category="relationship",
            source=f"rel:{subject}->{predicate}->{target}",
            content=f"{subject} {predicate} {target}",
            confidence=1.0 if is_v else 0.75,
            verification_status=RecordVerificationStatus.VERIFIED if is_v else RecordVerificationStatus.UNVERIFIED,
            verified_at=utc_now() if is_v else None,
            access_level=MemoryAccessLevel.INTERNAL,
            metadata={"subject": subject, "predicate": predicate, "target": target, "context": context or {}},
            provenance={"registered_by": verified_by or "system"},
        )
        return await self.store_record(record)

    async def store_organizational(
        self,
        policy_name: str,
        statement: str,
        enforcement_level: str = "mandatory",
        approved_by: str = "Boardroom",
    ) -> MemoryRecord:
        record = MemoryRecord(
            layer=MemoryLayer.ORGANIZATIONAL,
            category="policy",
            source=f"policy:{policy_name}",
            content=statement,
            confidence=1.0,
            verification_status=RecordVerificationStatus.VERIFIED,
            verified_at=utc_now(),
            access_level=MemoryAccessLevel.INTERNAL,
            metadata={"policy_name": policy_name, "enforcement_level": enforcement_level},
            provenance={"approved_by": approved_by},
        )
        return await self.store_record(record)

    def get_record(self, record_id: str) -> Optional[MemoryRecord]:
        return self._records.get(record_id)

    def query_records(
        self,
        query: Optional[str] = None,
        layer: Optional[MemoryLayer | str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None,
        verified_only: bool = False,
        requester_id: str = "BossAI",
        department: Optional[str] = None,
        projects: Optional[Sequence[str]] = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        layer_enum = None
        if layer:
            layer_enum = layer if isinstance(layer, MemoryLayer) else MemoryLayer(layer.lower())

        candidates = list(self._records.values())

        # RBAC and privacy filtering first
        accessible = self.access_controller.filter_records(
            candidates, requester_id=requester_id, department=department, projects=projects
        )

        filtered = []
        q_lower = query.lower() if query else None

        for rec in accessible:
            if layer_enum and rec.layer != layer_enum:
                continue
            if category and rec.category != category:
                continue
            if source and rec.source != source:
                continue
            if verified_only and not rec.is_verified:
                continue
            if q_lower and q_lower not in rec.content.lower() and q_lower not in rec.source.lower():
                continue
            filtered.append(rec)

        # Sort by verified first, then confidence desc, then creation desc
        filtered.sort(
            key=lambda r: (1 if r.is_verified else 0, r.confidence, r.created_at.timestamp()),
            reverse=True,
        )
        return filtered[:limit]

    def dispute_record(self, record_id: str, reason: str, disputer_id: str) -> bool:
        rec = self.get_record(record_id)
        if not rec:
            return False
        rec.mark_disputed(reason, disputer_id)
        return True

    def supersede_record(self, old_record_id: str, new_record_id: str) -> bool:
        old_rec = self.get_record(old_record_id)
        new_rec = self.get_record(new_record_id)
        if not old_rec or not new_rec:
            return False
        old_rec.mark_superseded(new_record_id)
        new_rec.related_record_ids.append(old_record_id)
        return True

    def get_layer_stats(self) -> dict[str, int]:
        stats = {layer.value: 0 for layer in MemoryLayer}
        for rec in self._records.values():
            stats[rec.layer.value] += 1
        return stats

