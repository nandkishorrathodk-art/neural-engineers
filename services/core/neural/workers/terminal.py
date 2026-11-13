from __future__ import annotations

import asyncio
import base64
import os
import shlex
import signal
import uuid
from typing import Annotated, Any

import httpx
from pydantic import BaseModel, Field, field_validator

from neural.containers.execution_limits import WORKER_COMMAND_REQUEST_TIMEOUT_SECONDS
from neural.workers.failures import TerminalCommandFailure

# Argument ceiling shared with the worker/verifier command contracts. Each
# materialized write command is bounded to this so a file's contents never
# overflow a single argv entry.
COMMAND_ARGUMENT_MAX_LENGTH = 8192
# Base64 payload carried by one materialized write command; leaves headroom for
# the surrounding ``python3 -c`` script within the argument ceiling.
WRITE_CHUNK_LENGTH = 4000
MAX_PLAN_COMMANDS = 24


class TerminalCommandPlan(BaseModel):
    commands: list[
        list[Annotated[str, Field(min_length=1, max_length=COMMAND_ARGUMENT_MAX_LENGTH)]]
    ] = Field(..., min_length=1, max_length=MAX_PLAN_COMMANDS)

    @field_validator("commands", mode="before")
    @classmethod
    def normalize_argv_commands(cls, commands: Any) -> Any:
        """Normalize model-emitted command strings without invoking a shell."""
        if not isinstance(commands, list):
            return commands
        normalized: list[Any] = []
        for command in commands:
            if isinstance(command, str):
                try:
                    normalized.append(shlex.split(command, posix=True))
                except ValueError as exc:
                    raise ValueError("Plan contains an invalid quoted command string.") from exc
            else:
                normalized.append(command)
        return normalized

    @field_validator("commands")
    @classmethod
    def restrict_commands(cls, commands: list[list[str]]) -> list[list[str]]:
        allowed = {"python", "python3", "pytest", "npm", "npx"}
        for command in commands:
            if not command or len(command) > 64 or command[0] not in allowed:
                raise ValueError("Plan contains an unsupported terminal command.")
        return commands


class WorkspaceFile(BaseModel):
    """A file the plan persists into the employee workspace."""

    path: str = Field(..., min_length=1, max_length=512)
    content: str = Field(default="", max_length=200_000)

    @field_validator("path")
    @classmethod
    def confine_to_workspace(cls, path: str) -> str:
        normalized = path.replace("\\", "/").strip()
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Workspace file paths must be relative and stay inside the workspace.")
        return normalized


class TerminalPlan(BaseModel):
    """Provider plan: workspace files plus the commands that run against them.

    Asking the model for file contents as plain strings (instead of embedding a
    whole source file inside a ``python3 -c`` string inside a JSON string) keeps
    quoting correct and lets the platform materialize the writes itself.
    """

    files: list[WorkspaceFile] = Field(default_factory=list, max_length=8)
    commands: list[
        list[Annotated[str, Field(min_length=1, max_length=COMMAND_ARGUMENT_MAX_LENGTH)]]
    ] = Field(default_factory=list, max_length=12)

    @field_validator("commands", mode="before")
    @classmethod
    def normalize_argv_commands(cls, commands: Any) -> Any:
        return TerminalCommandPlan.normalize_argv_commands(commands)

    @field_validator("commands")
    @classmethod
    def restrict_commands(cls, commands: list[list[str]]) -> list[list[str]]:
        return TerminalCommandPlan.restrict_commands(commands)

    def materialize(self) -> TerminalCommandPlan:
        writes: list[list[str]] = []
        for workspace_file in self.files:
            writes.extend(_write_commands(workspace_file.path, workspace_file.content))
        return TerminalCommandPlan(commands=[*writes, *self.commands])


def _write_commands(path: str, content: str) -> list[list[str]]:
    """Encode a file as argv ``python3 -c`` writes that persist to ``path``.

    Contents travel base64-encoded so no byte needs shell or quote escaping.
    Chunks append in order, and the first command truncates, so the final file
    always matches ``content`` exactly.
    """
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    chunks = [
        encoded[index : index + WRITE_CHUNK_LENGTH]
        for index in range(0, len(encoded), WRITE_CHUNK_LENGTH)
    ]
    if not chunks:
        chunks = [""]
    commands: list[list[str]] = []
    for index, chunk in enumerate(chunks):
        mode = "wb" if index == 0 else "ab"
        script = f"import base64; open({path!r}, {mode!r}).write(base64.b64decode({chunk!r}))"
        commands.append(["python3", "-c", script])
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
            raise ValueError(
                "Core API URL, worker identity, and a 32-character worker token are required."
            )
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

    async def _heartbeat_loop(
        self, task_id: str, fencing_token: int, lease_lost: asyncio.Event
    ) -> None:
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

    async def _plan_commands(
        self, task: dict[str, Any]
    ) -> tuple[TerminalCommandPlan, dict[str, Any]]:
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
                    raise TerminalCommandFailure(
                        f"Terminal command exited with code {result.get('exit_code')}: "
                        f"{str(result.get('stderr', ''))[-1000:]}",
                        exit_code=int(result.get("exit_code")),
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
        except TerminalCommandFailure as exc:
            if started:
                await self._report_failure(task_id, fence, str(exc), exit_code=exc.exit_code)
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

    async def _report_failure(
        self, task_id: str, fencing_token: int, message: str, exit_code: int | None = None
    ) -> None:
        payload: dict[str, Any] = {"fencing_token": fencing_token, "error_message": message[:4000]}
        if exit_code is not None:
            payload["exit_code"] = exit_code
        try:
            await self._post(
                f"/internal/worker/tasks/{task_id}/fail",
                payload,
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
