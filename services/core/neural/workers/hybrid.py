"""Hybrid execution coordinator: one task, both GUI and terminal capabilities.

A hybrid task needs a graphical desktop *and* a terminal environment. This
coordinator runs both kinds of work under a single task owner and produces one
coherent execution history:

1. **terminal phase** — generate and run the task's terminal plan (build/prepare
   the workspace) inside the employee container;
2. **gui phase** — drive the live desktop through the real pixel/motor loop;
3. **verification phase** — run terminal verification commands against the
   workspace the GUI phase produced.

Every phase is recorded in order on one receipt, so an independent verifier sees
the whole hybrid timeline rather than two disconnected attempts.
"""

from __future__ import annotations

from typing import Any

import httpx

from neural.logger import get_logger

logger = get_logger("ancos.workers.hybrid")


class HybridExecutionError(RuntimeError):
    """Raised when a hybrid phase cannot complete."""

    def __init__(self, message: str, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class HybridExecutionCoordinator:
    def __init__(self, worker: Any) -> None:
        self.worker = worker

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.worker._post(path, payload)

    async def _run_terminal_phase(
        self, task: dict[str, Any], fence: int, phase_name: str
    ) -> dict[str, Any]:
        task_id = task["task_id"]
        plan_response = await self._post(
            f"/internal/worker/tasks/{task_id}/plan",
            {"fencing_token": fence},
        )
        commands = plan_response.get("plan", {}).get("commands", [])
        command_receipts: list[dict[str, Any]] = []
        for command in commands:
            result = await self._post(
                f"/internal/worker/tasks/{task_id}/exec",
                {"fencing_token": fence, "command": command},
            )
            command_receipts.append({**result, "command": command})
            if result.get("exit_code") != 0:
                raise HybridExecutionError(
                    f"[{phase_name}] terminal command exited with code {result.get('exit_code')}: "
                    f"{str(result.get('stderr', ''))[-1000:]}",
                    exit_code=int(result.get("exit_code")),
                )
        return {
            "kind": "terminal",
            "phase": phase_name,
            "commands": command_receipts,
            "model": plan_response.get("model"),
        }

    async def _run_gui_phase(self, task: dict[str, Any], fence: int) -> dict[str, Any]:
        task_id = task["task_id"]
        plan_response = await self._post(
            f"/internal/worker/tasks/{task_id}/gui/plan",
            {"fencing_token": fence},
        )
        steps = plan_response.get("steps") or []
        result = await self._post(
            f"/internal/worker/tasks/{task_id}/gui/execute",
            {
                "fencing_token": fence,
                "steps": steps,
                "expected_text": task.get("expected_text"),
                "display": getattr(self.worker, "display", None),
            },
        )
        gui_result = result.get("result", {})
        if gui_result.get("status") != "success":
            raise HybridExecutionError(
                f"[gui] desktop interaction failed: {gui_result.get('error') or 'no visual change verified'}"
            )
        return {"kind": "gui", "phase": "interaction", "steps": steps, "result": gui_result}

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        fence = int(task["fencing_token"])
        phases: list[dict[str, Any]] = []
        try:
            phases.append(await self._run_terminal_phase(task, fence, "prepare"))
            phases.append(await self._run_gui_phase(task, fence))
            phases.append(await self._run_terminal_phase(task, fence, "verify"))
        except (HybridExecutionError, httpx.HTTPError) as exc:
            logger.error("Hybrid task failed", task_id=task.get("task_id"), error=str(exc))
            return {
                "executor": "gui-worker",
                "execution_mode": "hybrid",
                "phases": phases,
                "error": str(exc),
                "verification_status": "awaiting_independent_verifier",
                "passed": False,
            }
        return {
            "executor": "gui-worker",
            "execution_mode": "hybrid",
            "phases": phases,
            "phase_count": len(phases),
            "capabilities_used": ["terminal", "gui"],
            "verification_status": "awaiting_independent_verifier",
            "passed": True,
        }
