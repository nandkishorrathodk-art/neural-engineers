"""Long-running GUI worker: claims GUI/hybrid tasks and drives a live desktop.

This is the workforce consumer that was missing (gap G-1.1). It runs co-located
with the employee's graphical desktop, so it drives the real X display directly:
it claims a task, asks Core API for a concrete step plan, executes those steps
against the live screen, and submits a receipt carrying before/after screenshot
hashes and per-step motor receipts.

Hybrid tasks are claimed here too and delegated to :class:`HybridExecutionCoordinator`,
which interleaves GUI steps with terminal commands while preserving one execution
history.
"""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from typing import Any

import httpx

from neural.logger import get_logger

logger = get_logger("ancos.workers.gui")


class GuiWorker:
    def __init__(
        self,
        api_url: str,
        worker_id: str,
        token: str,
        display: str | None = None,
        poll_interval: float = 3.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_url or not worker_id or len(token) < 32:
            raise ValueError("Core API URL, worker identity, and a 32-character worker token are required.")
        self.api_url = api_url.rstrip("/")
        self.worker_id = worker_id
        self.display = display or os.environ.get("DISPLAY", ":1")
        self.instance_id = uuid.uuid4().hex
        self.client = client or httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(180.0, connect=5.0),
        )
        self.poll_interval = max(0.2, min(poll_interval, 60.0))
        self._stopping = asyncio.Event()

    async def close(self) -> None:
        self._stopping.set()
        await self.client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(path, json=payload)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError("Core API returned an invalid worker response.")
        return result

    async def _heartbeat_loop(self, task_id: str, fencing_token: int, lease_lost: asyncio.Event) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=20.0)
                lease_lost.set()
                return
            except TimeoutError:
                pass
            try:
                await self._post(
                    f"/internal/worker/tasks/{task_id}/heartbeat",
                    {"fencing_token": fencing_token},
                )
            except httpx.HTTPError:
                lease_lost.set()
                return

    async def execute_claim(self, task: dict[str, Any]) -> None:
        task_id = task["task_id"]
        fence = int(task["fencing_token"])
        mode = str(task.get("execution_mode", "gui")).lower()
        started = False
        heartbeat: asyncio.Task[None] | None = None
        lease_lost = asyncio.Event()
        try:
            await self._post(f"/internal/worker/tasks/{task_id}/start", {"fencing_token": fence})
            started = True
            heartbeat = asyncio.create_task(self._heartbeat_loop(task_id, fence, lease_lost))

            if mode == "hybrid":
                from neural.workers.hybrid import HybridExecutionCoordinator

                coordinator = HybridExecutionCoordinator(self)
                receipt = await coordinator.execute(task)
            else:
                receipt = await self._execute_gui(task, fence)

            if lease_lost.is_set():
                return
            await self._post(
                f"/internal/worker/tasks/{task_id}/submit",
                {"fencing_token": fence, "execution_receipt": receipt},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (409, 503):
                return
            if started:
                await self._report_failure(task_id, fence, str(exc))
            else:
                raise
        except Exception as exc:
            if started:
                await self._report_failure(task_id, fence, str(exc))
            else:
                raise
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _execute_gui(self, task: dict[str, Any], fence: int) -> dict[str, Any]:
        plan_response = await self._post(
            f"/internal/worker/tasks/{task['task_id']}/gui/plan",
            {"fencing_token": fence},
        )
        steps = plan_response.get("steps") or []
        result = await self._post(
            f"/internal/worker/tasks/{task['task_id']}/gui/execute",
            {
                "fencing_token": fence,
                "steps": steps,
                "expected_text": task.get("expected_text"),
                "display": self.display,
            },
        )
        gui_result = result.get("result", {})
        passed = gui_result.get("status") == "success"
        return {
            "executor": "gui-worker",
            "execution_mode": "gui",
            "display": self.display,
            "gui": gui_result,
            "verification_status": "awaiting_independent_verifier",
            "passed": passed,
        }

    async def _report_failure(self, task_id: str, fencing_token: int, message: str) -> None:
        try:
            await self._post(
                f"/internal/worker/tasks/{task_id}/fail",
                {"fencing_token": fencing_token, "error_message": message[:4000]},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (409, 503):
                raise

    async def run(self) -> None:
        heartbeat_task = asyncio.create_task(self._runtime_heartbeat_loop())
        try:
            while not self._stopping.is_set():
                try:
                    response = await self._post(
                        "/internal/worker/tasks/claim",
                        {"execution_modes": ["gui", "hybrid"]},
                    )
                    task = response.get("task")
                    if task:
                        await self.execute_claim(task)
                        continue
                except httpx.HTTPError:
                    pass
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    continue
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _runtime_heartbeat_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._post(
                    "/internal/worker/runtime/heartbeat",
                    {"instance_id": self.instance_id},
                )
            except httpx.HTTPError:
                pass
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=20.0)
            except TimeoutError:
                continue


async def main() -> None:
    worker = GuiWorker(
        api_url=os.environ.get("ANCOS_CORE_API_URL", "http://127.0.0.1:12000"),
        worker_id=os.environ.get("ANCOS_WORKER_ID", "gui-worker"),
        token=os.environ.get("WORKER_API_TOKEN", ""),
        display=os.environ.get("DISPLAY", ":1"),
        poll_interval=float(os.environ.get("ANCOS_WORKER_POLL_SECONDS", "3")),
    )
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(stop_signal, worker._stopping.set)
        except NotImplementedError:
            pass
    try:
        await worker.run()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
