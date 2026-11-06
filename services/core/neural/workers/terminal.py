from __future__ import annotations

import asyncio
import os
import signal
import uuid
from typing import Annotated, Any

import httpx
from pydantic import BaseModel, Field, field_validator

from neural.containers.execution_limits import WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS


class TerminalCommandPlan(BaseModel):
    commands: list[list[Annotated[str, Field(min_length=1, max_length=8192)]]] = Field(
        ..., min_length=1, max_length=12
    )

    @field_validator("commands")
    @classmethod
    def restrict_commands(cls, commands: list[list[str]]) -> list[list[str]]:
        allowed = {"python", "python3", "pytest", "npm", "npx"}
        for command in commands:
            if not command or len(command) > 64 or command[0] not in allowed:
                raise ValueError("Plan contains an unsupported terminal command.")
        return commands


class TerminalWorker:
    def __init__(
        self,
        api_url: str,
        worker_id: str,
        token: str,
        poll_interval: float = 3.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_url or not worker_id or len(token) < 32:
            raise ValueError("Core API URL, worker identity, and a 32-character worker token are required.")
        self.api_url = api_url.rstrip("/")
        self.worker_id = worker_id
        self.instance_id = uuid.uuid4().hex
        self.client = client or httpx.AsyncClient(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(75.0, connect=5.0),
        )
        self.poll_interval = max(0.2, min(poll_interval, 60.0))
        self._stopping = asyncio.Event()

    async def close(self) -> None:
        self._stopping.set()
        await self.client.aclose()

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        if timeout is None:
            response = await self.client.post(path, json=payload)
        else:
            response = await self.client.post(path, json=payload, timeout=timeout)
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
            except httpx.HTTPStatusError:
                lease_lost.set()
                return
            except httpx.HTTPError:
                lease_lost.set()
                return

    async def _plan_commands(self, task: dict[str, Any]) -> tuple[TerminalCommandPlan, dict[str, Any]]:
        response = await self._post(
            f"/internal/worker/tasks/{task['task_id']}/plan",
            {"fencing_token": int(task["fencing_token"])},
        )
        plan = TerminalCommandPlan.model_validate(response["plan"])
        model = response["model"]
        metadata = {
            "provider": model["provider"],
            "model": model["model"],
            "input_tokens": model["input_tokens"],
            "output_tokens": model["output_tokens"],
            "cost_usd": model["cost_usd"],
        }
        return plan, metadata

    async def execute_claim(self, task: dict[str, Any]) -> None:
        task_id = task["task_id"]
        fence = int(task["fencing_token"])
        started = False
        heartbeat: asyncio.Task[None] | None = None
        lease_lost = asyncio.Event()
        try:
            await self._post(f"/internal/worker/tasks/{task_id}/start", {"fencing_token": fence})
            started = True
            heartbeat = asyncio.create_task(self._heartbeat_loop(task_id, fence, lease_lost))
            plan, model_receipt = await self._plan_commands(task)
            command_receipts: list[dict[str, Any]] = []
            for command in plan.commands:
                if lease_lost.is_set():
                    return
                result = await self._post(
                    f"/internal/worker/tasks/{task_id}/exec",
                    {"fencing_token": fence, "command": command},
                    timeout=httpx.Timeout(WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS, connect=5.0),
                )
                command_receipts.append({**result, "command": command})
                if result.get("exit_code") != 0:
                    raise RuntimeError(
                        f"Terminal command exited with code {result.get('exit_code')}: "
                        f"{str(result.get('stderr', ''))[-1000:]}"
                    )
            if lease_lost.is_set():
                return
            await self._post(
                f"/internal/worker/tasks/{task_id}/submit",
                {
                    "fencing_token": fence,
                    "execution_receipt": {
                        "executor": "terminal-worker",
                        "execution_mode": "terminal",
                        "model": model_receipt,
                        "planned_commands": plan.commands,
                        "commands": command_receipts,
                        "verification_status": "awaiting_independent_verifier",
                        "passed": False,
                    },
                },
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
                        {"execution_modes": ["terminal"]},
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
    worker = TerminalWorker(
        api_url=os.environ.get("ANCOS_CORE_API_URL", "http://127.0.0.1:12000"),
        worker_id=os.environ.get("ANCOS_WORKER_ID", "terminal-worker"),
        token=os.environ.get("WORKER_API_TOKEN", ""),
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
