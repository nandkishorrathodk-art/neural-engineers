from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import get_resource_lease_repo, require_worker_token
from neural.api.schemas.requests import ResourceLeaseClaimRequest, ResourceLeaseTokenRequest
from neural.db.repositories import ResourceLeaseRepository

router = APIRouter(
    prefix="/internal/resources/leases",
    tags=["Resource leases"],
    dependencies=[Depends(require_worker_token)],
)


@router.post("/claim")
async def claim_resource_lease(
    request: ResourceLeaseClaimRequest,
    lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
):
    lease = await lease_repo.claim(
        resource_key=request.resource_key,
        employee_id=request.employee_id,
        task_id=request.task_id,
        session_id=request.session_id,
        lease_seconds=request.lease_seconds,
        resource_reservation=request.resource_reservation,
    )
    if lease is None:
        raise HTTPException(status_code=409, detail="Resource or employee already has an active lease.")
    return lease


@router.post("/{lease_id}/heartbeat")
async def heartbeat_resource_lease(
    lease_id: str,
    request: ResourceLeaseTokenRequest,
    lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
):
    expiry = await lease_repo.heartbeat(lease_id, request.fencing_token)
    if expiry is None:
        raise HTTPException(status_code=409, detail="Resource lease expired, was released, or has a stale fencing token.")
    return {"lease_id": lease_id, "fencing_token": request.fencing_token, "lease_expires_at": expiry}


@router.post("/{lease_id}/release")
async def release_resource_lease(
    lease_id: str,
    request: ResourceLeaseTokenRequest,
    lease_repo: ResourceLeaseRepository = Depends(get_resource_lease_repo),
):
    released = await lease_repo.release(lease_id, request.fencing_token)
    if not released:
        raise HTTPException(status_code=409, detail="Resource lease is stale or already released.")
    return {"lease_id": lease_id, "status": "released"}
