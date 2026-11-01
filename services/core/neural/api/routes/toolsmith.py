from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import get_tool_repo, get_toolsmith_engine
from neural.api.schemas.requests import AuthorToolRequest, ExecuteToolRequest, TestToolRequest
from neural.api.schemas.responses import ToolDetailResponse, ToolExecutionResponse
from neural.db.models import ToolRecord
from neural.db.repositories import ToolRepository
from neural.tools.engine import ToolsmithEngine

router = APIRouter(prefix="/toolsmith", tags=["Toolsmith Engine"])


def serialize_tool(t: ToolRecord) -> ToolDetailResponse:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return ToolDetailResponse(
        id=t.id or t.tool_id or str(uuid.uuid4()),
        tool_id=t.tool_id or "",
        name=t.name or "",
        version=t.version or "1.0.0",
        description=t.description or "",
        category=t.category or "utility",
        code=t.code or "",
        test_code=t.test_code or "",
        interface_schema=t.interface_schema or {},
        dependencies=t.dependencies or [],
        author_id=t.author_id or "autonomous_toolsmith",
        status=t.status or "draft",
        safety_status=t.safety_status or "pending",
        safety_violations=t.safety_violations or [],
        verification_receipt=t.verification_receipt or {},
        created_at=t.created_at or now,
        updated_at=t.updated_at or now,
    )


@router.post("/author", response_model=ToolDetailResponse)
async def author_tool(
    req: AuthorToolRequest,
    engine: ToolsmithEngine = Depends(get_toolsmith_engine),
):
    """
    Autonomously authors, validates AST safety envelope, compiles,
    and runs sandbox testing on a new specialized tool.
    """
    tool, _ = await engine.author_tool(
        name=req.name,
        code=req.code,
        test_code=req.test_code,
        description=req.description,
        version=req.version,
        category=req.category,
        interface_schema=req.interface_schema,
        dependencies=req.dependencies,
        author_id=req.author_id,
        auto_test=req.auto_test,
        timeout=req.timeout,
    )
    return serialize_tool(tool)


@router.post("/test/{tool_id_or_name}", response_model=ToolDetailResponse)
async def test_tool(
    tool_id_or_name: str,
    req: TestToolRequest = TestToolRequest(),
    engine: ToolsmithEngine = Depends(get_toolsmith_engine),
    tool_repo: ToolRepository = Depends(get_tool_repo),
):
    """
    Runs automated sandbox verification tests on an existing authored tool.
    """
    try:
        await engine.test_tool(
            tool_id_or_name=tool_id_or_name,
            test_override=req.test_code_override,
            timeout=req.timeout,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    tool = await tool_repo.get_by_tool_id(tool_id_or_name)
    if not tool:
        tool = await tool_repo.get_by_id(tool_id_or_name)
    if not tool:
        tool = await tool_repo.get_latest_verified_tool(tool_id_or_name)
    if not tool:
        tool = await engine.registry.get_tool(tool_id_or_name, verified_only=False)
    if not tool:
        all_tools = await tool_repo.list_tools(limit=100)
        for t in all_tools:
            if t.name == tool_id_or_name:
                tool = t
                break

    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id_or_name}' not found")

    return serialize_tool(tool)


@router.get("/tools", response_model=list[ToolDetailResponse])
async def list_tools(
    category: str | None = Query(None, description="Filter by category (utility, security, data)"),
    status: str | None = Query(None, description="Filter by status (verified, draft, rejected, deprecated)"),
    verified_only: bool = Query(False, description="Filter only verified tools"),
    limit: int = Query(100, ge=1, le=200),
    engine: ToolsmithEngine = Depends(get_toolsmith_engine),
):
    """
    Lists tools in registry with optional category and status filters.
    """
    tools = await engine.registry.list_tools(
        category=category,
        status=status,
        verified_only=verified_only,
        limit=limit,
    )
    return [serialize_tool(t) for t in tools]


@router.get("/tools/{tool_id_or_name}", response_model=ToolDetailResponse)
async def get_tool(
    tool_id_or_name: str,
    version: str | None = Query(None, description="Optional specific semver version"),
    tool_repo: ToolRepository = Depends(get_tool_repo),
):
    """
    Retrieves full details and interface specification of a tool.
    """
    tool: ToolRecord | None = None
    if version:
        tool = await tool_repo.get_by_name_and_version(tool_id_or_name, version)
    if not tool:
        tool = await tool_repo.get_by_tool_id(tool_id_or_name)
    if not tool:
        tool = await tool_repo.get_by_id(tool_id_or_name)
    if not tool:
        tool = await tool_repo.get_latest_verified_tool(tool_id_or_name)
    if not tool:
        all_tools = await tool_repo.list_tools(limit=100)
        for t in all_tools:
            if t.name == tool_id_or_name:
                tool = t
                break

    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id_or_name}' not found")

    return serialize_tool(tool)


@router.post("/execute", response_model=ToolExecutionResponse)
async def execute_tool(
    req: ExecuteToolRequest,
    engine: ToolsmithEngine = Depends(get_toolsmith_engine),
):
    """
    Executes a verified tool function inside the isolated workstation sandbox.
    """
    try:
        res = await engine.execute_tool(
            tool_name_or_id=req.tool_name_or_id,
            function_name=req.function_name,
            arguments=req.arguments,
            timeout=req.timeout,
        )
        return ToolExecutionResponse(
            status=res.get("status", "error"),
            result=res.get("result"),
            error=res.get("error"),
            receipt=res.get("receipt"),
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/tools/{tool_id_or_name}", response_model=ToolDetailResponse)
async def deprecate_tool(
    tool_id_or_name: str,
    tool_repo: ToolRepository = Depends(get_tool_repo),
):
    """
    Deprecates a tool from active registry.
    """
    dep = await tool_repo.deprecate_tool(tool_id_or_name)
    if not dep:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id_or_name}' not found")
    return serialize_tool(dep)
