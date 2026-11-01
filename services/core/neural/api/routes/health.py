from typing import Any

from fastapi import APIRouter, Depends, Response, status
from neural.api.deps import get_db_session
from neural.config import get_settings
from neural.security.kill_switch import get_kill_switch
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
@router.get("/")
async def health_check() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "version": "0.1.0",
    }


@router.get("/live")
async def liveness_probe() -> dict[str, str]:
    """Kubernetes/container liveness probe confirming process is responsive."""
    return {"status": "alive", "timestamp": "ok"}


@router.get("/ready")
async def readiness_probe(
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Kubernetes/load-balancer readiness probe.
    Validates database connectivity and verifies that the Emergency Kill Switch is NOT active.
    Returns 503 if the database is unreachable or the system has been halted.
    """
    db_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    kill_switch = get_kill_switch()
    is_halted = kill_switch.is_halted

    if not db_ok or is_halted:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not_ready",
            "database_connected": db_ok,
            "kill_switch_active": is_halted,
            "reason": "Emergency halt active" if is_halted else "Database unavailable",
        }

    return {
        "status": "ready",
        "database_connected": True,
        "kill_switch_active": False,
    }



