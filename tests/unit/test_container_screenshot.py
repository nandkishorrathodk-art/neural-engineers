from __future__ import annotations

import base64

import pytest
from neural.containers.manager import ContainerManager
from neural.exceptions import ContainerError


@pytest.mark.asyncio
async def test_capture_screenshot_accepts_only_broker_png(monkeypatch):
    manager = ContainerManager()
    png_bytes = b"\x89PNG\r\n\x1a\nfixture"
    calls = []

    async def execute_command(container_id, command, workdir=None):
        calls.append((container_id, command, workdir))
        return {"exit_code": 0, "stdout": base64.b64encode(png_bytes).decode(), "stderr": ""}

    monkeypatch.setattr(manager, "execute_command", execute_command)
    result = await manager.capture_screenshot("ancos-desktop-anc-emp-test")

    assert result == png_bytes
    assert calls[0][1][0:2] == ["sh", "-lc"]
    assert "command -v scrot" in calls[0][1][2]
    assert "scrot - | base64 -w 0" in calls[0][1][2]


@pytest.mark.asyncio
async def test_capture_screenshot_rejects_non_png_output(monkeypatch):
    manager = ContainerManager()

    async def execute_command(container_id, command, workdir=None):
        return {"exit_code": 0, "stdout": base64.b64encode(b"not an image").decode(), "stderr": ""}

    monkeypatch.setattr(manager, "execute_command", execute_command)
    with pytest.raises(ContainerError, match="not a PNG"):
        await manager.capture_screenshot("ancos-desktop-anc-emp-test")
