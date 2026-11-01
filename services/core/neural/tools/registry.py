from __future__ import annotations

import re
from typing import Any, Sequence

from neural.db.models import ToolRecord
from neural.db.repositories import ToolRepository
from neural.logger import get_logger

logger = get_logger("ancos.tools.registry")

SEMVER_REGEX = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def parse_semver(version: str) -> tuple[int, int, int]:
    m = SEMVER_REGEX.match(version)
    if not m:
        return (1, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


class ToolRegistry:
    """
    Dynamic, versioned tool registry with interface discovery and dependency isolation.
    """

    def __init__(self, tool_repo: ToolRepository | None = None) -> None:
        self.tool_repo = tool_repo
        self._cache: dict[str, dict[str, ToolRecord]] = {}  # name -> {version: record}

    def _add_to_cache(self, tool: ToolRecord) -> None:
        if tool.name not in self._cache:
            self._cache[tool.name] = {}
        self._cache[tool.name][tool.version] = tool

    async def register(self, tool: ToolRecord) -> ToolRecord:
        self._add_to_cache(tool)
        logger.info(
            "Registered tool in registry",
            name=tool.name,
            version=tool.version,
            status=tool.status,
            tool_id=tool.tool_id,
        )
        return tool

    async def get_tool(
        self,
        name: str,
        version: str | None = None,
        verified_only: bool = False,
    ) -> ToolRecord | None:
        # Check cache
        if name in self._cache:
            versions = self._cache[name]
            if version:
                tool = versions.get(version)
                if tool and (not verified_only or tool.status == "verified"):
                    return tool
            else:
                # Find highest semver
                sorted_vers = sorted(versions.keys(), key=parse_semver, reverse=True)
                for v in sorted_vers:
                    t = versions[v]
                    if not verified_only or t.status == "verified":
                        return t

        # If repo available, fetch from DB
        if self.tool_repo:
            if version:
                tool = await self.tool_repo.get_by_name_and_version(name, version)
                if tool and (not verified_only or tool.status == "verified"):
                    self._add_to_cache(tool)
                    return tool
            else:
                if verified_only:
                    tool = await self.tool_repo.get_latest_verified_tool(name)
                    if tool:
                        self._add_to_cache(tool)
                        return tool
                else:
                    tools = await self.tool_repo.list_tools(limit=100)
                    matching = [t for t in tools if t.name == name]
                    if matching:
                        matching.sort(key=lambda t: parse_semver(t.version), reverse=True)
                        tool = matching[0]
                        self._add_to_cache(tool)
                        return tool
        return None

    async def list_tools(
        self,
        category: str | None = None,
        status: str | None = None,
        verified_only: bool = False,
        limit: int = 100,
    ) -> Sequence[ToolRecord]:
        if self.tool_repo:
            target_status = "verified" if verified_only else status
            records = await self.tool_repo.list_tools(
                category=category,
                status=target_status,
                limit=limit,
            )
            for r in records:
                self._add_to_cache(r)
            return records

        # Fallback to in-memory cache
        results: list[ToolRecord] = []
        for name, versions in self._cache.items():
            for ver, tool in versions.items():
                if category and tool.category != category:
                    continue
                if verified_only and tool.status != "verified":
                    continue
                if status and tool.status != status:
                    continue
                results.append(tool)
        return results[:limit]

    async def discover_interfaces(
        self,
        category: str | None = None,
        verified_only: bool = True,
    ) -> list[dict[str, Any]]:
        tools = await self.list_tools(category=category, verified_only=verified_only)
        interfaces: list[dict[str, Any]] = []
        for tool in tools:
            schema = tool.interface_schema or {}
            interfaces.append({
                "tool_id": tool.tool_id,
                "name": tool.name,
                "version": tool.version,
                "category": tool.category,
                "description": tool.description,
                "schema": schema,
                "status": tool.status,
                "dependencies": tool.dependencies or [],
            })
        return interfaces

    @staticmethod
    def validate_dependencies(dependencies: list[str]) -> tuple[bool, list[str]]:
        """
        Validates package dependencies for isolation and safe names.
        """
        disallowed = {"os", "sys", "subprocess", "pty", "posix", "ctypes"}
        violations = []
        for dep in dependencies:
            clean_dep = dep.strip().lower().split("==")[0].split(">=")[0].split("<=")[0]
            if clean_dep in disallowed:
                violations.append(f"Forbidden dependency: '{clean_dep}' cannot be isolated")
            elif not re.match(r"^[a-zA-Z0-9_\-\.]+$", clean_dep):
                violations.append(f"Invalid dependency specification: '{dep}'")
        return len(violations) == 0, violations

