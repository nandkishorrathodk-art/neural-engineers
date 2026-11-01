from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Response
from neural.api.deps import get_db_session
from neural.db.repositories import AuditRepository
from neural.observability.audit import AuditLogChainVerifier
from neural.observability.metrics import PrometheusExporter
from neural.observability.telemetry import SystemTelemetryCollector
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["Enterprise Observability"])


@router.get("/metrics")
async def get_prometheus_metrics(
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """
    Exposes real-time Prometheus metrics for Prometheus server scraping.
    Content-Type: text/plain; version=0.0.4; charset=utf-8
    """
    metrics_text = await PrometheusExporter.collect_metrics_text(session)
    return Response(
        content=metrics_text,
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/observability/telemetry")
async def get_system_telemetry() -> dict[str, Any]:
    """
    Returns live system telemetry feeds (CPU, Memory, Disk, Network)
    for interactive Next.js dashboard gauges and health monitoring.
    """
    return SystemTelemetryCollector.collect_all_telemetry()


@router.get("/observability/audit-logs")
async def list_audit_logs(
    actor: Optional[str] = Query(None, description="Filter by actor"),
    action: Optional[str] = Query(None, description="Filter by action name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Returns chronologically ordered audit trail entries."""
    repo = AuditRepository(session)
    if actor:
        records = await repo.list_by_actor(actor, limit=limit)
    elif action:
        records = await repo.list_by_action(action, limit=limit)
    else:
        records = await repo.list_all(limit=limit, offset=offset)

    return [
        {
            "id": r.id,
            "actor": r.actor,
            "action": r.action,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "details": r.details,
            "request_hash": r.request_hash,
            "payload": r.payload,
            "signature": r.signature,
            "previous_hash": r.previous_hash,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for r in records
    ]


@router.get("/observability/audit-logs/verify")
async def verify_audit_trail_chain(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Cryptographically verifies the SHA-256 tamper-evident hash chain of all AuditLog records.
    """
    repo = AuditRepository(session)
    records = await repo.list_all(limit=1000)
    ordered_records = AuditLogChainVerifier.order_chain(records)
    is_valid, issues = AuditLogChainVerifier.verify_records(ordered_records)
    return {
        "valid": is_valid,
        "total_records": len(ordered_records),
        "issues": issues,
        "status": "tamper_evident_verified" if is_valid else "integrity_violation",
    }
