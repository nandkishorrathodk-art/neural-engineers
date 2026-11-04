from typing import Any

from fastapi import APIRouter, Depends, Response, status
from neural.api.deps import get_db_session
from neural.config import get_settings
from neural.db.repositories import EmergencyStopRepository
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
    emergency_state_ok = False
    is_halted = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
        durable_state = await EmergencyStopRepository(session).get_state()
        get_kill_switch().sync_state(durable_state)
        is_halted = bool(durable_state["active"])
        emergency_state_ok = True
    except Exception:
        # A replica must not advertise readiness if the durable safety latch
        # cannot be read, even when its local in-memory latch is clear.
        emergency_state_ok = False

    if not db_ok or not emergency_state_ok or is_halted:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        if is_halted:
            reason = "Emergency halt active"
        elif not db_ok:
            reason = "Database unavailable"
        else:
            reason = "Emergency-stop state unavailable"
        return {
            "status": "not_ready",
            "database_connected": db_ok,
            "kill_switch_active": is_halted,
            "emergency_stop_state_available": emergency_state_ok,
            "reason": reason,
        }

    return {
        "status": "ready",
        "database_connected": True,
        "kill_switch_active": False,
        "emergency_stop_state_available": True,
    }



