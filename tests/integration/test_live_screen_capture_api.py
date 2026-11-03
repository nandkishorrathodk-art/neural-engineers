from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import (
    get_container_manager,
    get_container_repo,
    get_employee_repo,
    get_resource_lease_repo,
    require_owner_identity,
)
from neural.api.main import create_app
from PIL import Image


@pytest.mark.asyncio
async def test_screen_capture_api_returns_hash_of_live_png_bytes():
    image = Image.fromarray(np.full((12, 20, 3), (12, 90, 180), dtype=np.uint8), mode="RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG")

    class FakeContainerManager:
        async def get_desktop_by_employee_id(self, employee_id, **kwargs):
            assert employee_id == "ANC-EMP-TEST"
            return {"docker_id": "ancos-desktop-anc-emp-test", "status": "running"}

        async def capture_screenshot(self, docker_id):
            assert docker_id == "ancos-desktop-anc-emp-test"
            return buffer.getvalue()

    class FakeResourceLeaseRepository:
        async def get_by_session_id(self, _session_id):
            return None

        async def claim(self, **kwargs):
            return {
                "lease_id": "lease-screen-test",
                "resource_key": "desktop:test-host:slot-1",
                "employee_id": kwargs["employee_id"],
                "session_id": kwargs["session_id"],
                "fencing_token": 1,
                "status": "active",
                "lease_expires_at": "2099-01-01T00:00:00",
            }

    app = create_app()
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"
    app.dependency_overrides[get_container_manager] = lambda: FakeContainerManager()
    app.dependency_overrides[get_container_repo] = lambda: object()
    app.dependency_overrides[get_employee_repo] = lambda: object()
    app.dependency_overrides[get_resource_lease_repo] = lambda: FakeResourceLeaseRepository()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/gui/screen/capture?employee_id=ANC-EMP-TEST")

    assert response.status_code == 200
    body = response.json()
    assert body["resolution"] == [20, 12]
    assert len(body["frame_hash"]) == 64
    assert body["capture_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_screen_capture_api_does_not_fall_back_to_synthetic_desktop():
    class MissingDesktopManager:
        async def get_desktop_by_employee_id(self, employee_id, **kwargs):
            return None

    app = create_app()
    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"
    app.dependency_overrides[get_container_manager] = lambda: MissingDesktopManager()
    app.dependency_overrides[get_container_repo] = lambda: object()
    app.dependency_overrides[get_employee_repo] = lambda: object()
    app.dependency_overrides[get_resource_lease_repo] = lambda: object()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/gui/screen/capture?employee_id=ANC-EMP-TEST")

    assert response.status_code == 409
    assert "not running" in response.json()["detail"]
