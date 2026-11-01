from __future__ import annotations

from typing import Any, Sequence

from neural.db.models import MethodRecord
from neural.db.repositories import MethodRepository
from neural.logger import get_logger

logger = get_logger("ancos.methods.catalog")


class MethodologyCatalog:
    """
    Active Organizational Methodology Catalog.
    Stores and serves empirical-benchmark verified problem-solving methods for the workforce.
    """

    def __init__(self, method_repo: MethodRepository | None = None) -> None:
        self.method_repo = method_repo
        self._in_memory_catalog: dict[str, MethodRecord] = {}

    def register_in_memory(self, method: MethodRecord) -> None:
        self._in_memory_catalog[method.method_id] = method

    async def list_active_methods(self, domain: str | None = None) -> Sequence[MethodRecord]:
        if self.method_repo:
            methods = await self.method_repo.list_active_catalog(domain=domain)
            for m in methods:
                self._in_memory_catalog[m.method_id] = m
            return methods

        results = [
            m for m in self._in_memory_catalog.values()
            if m.status == "promoted" and (not domain or m.domain == domain)
        ]
        return results

    async def get_method(self, method_id_or_name: str, promoted_only: bool = False) -> MethodRecord | None:
        if method_id_or_name in self._in_memory_catalog:
            rec = self._in_memory_catalog[method_id_or_name]
            if not promoted_only or rec.status == "promoted":
                return rec

        for m in self._in_memory_catalog.values():
            if m.name == method_id_or_name and m.status == "promoted":
                return m

        if not promoted_only:
            for m in self._in_memory_catalog.values():
                if m.name == method_id_or_name:
                    return m

        if self.method_repo:
            m = await self.method_repo.get_by_method_id(method_id_or_name)
            if not m:
                m = await self.method_repo.get_by_id(method_id_or_name)
            if not m:
                target_status = "promoted" if promoted_only else None
                methods = await self.method_repo.list_methods(status=target_status)
                for candidate in methods:
                    if candidate.name == method_id_or_name:
                        m = candidate
                        break
            if m and (not promoted_only or m.status == "promoted"):
                self._in_memory_catalog[m.method_id] = m
                return m
        return None

    async def get_execution_plan(self, method_id: str) -> dict[str, Any] | None:
        method = await self.get_method(method_id)
        if not method:
            return None

        return {
            "method_id": method.method_id,
            "name": method.name,
            "domain": method.domain,
            "objective": method.objective,
            "strategy_type": method.strategy_type,
            "total_steps": len(method.steps),
            "steps": method.steps,
            "fallback_strategy": method.fallback_strategy,
            "version": method.version,
            "promoted_at": method.promoted_at.isoformat() if method.promoted_at else None,
        }

