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
from neural.verification.test_commands import persisted_paths

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
        self,
        task: dict[str, Any],
        fence: int,
        phase_name: str,
        exclude_text: str | None = None,
        exclude_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        task_id = task["task_id"]
        plan_payload: dict[str, Any] = {"fencing_token": fence, "phase": phase_name}
        if exclude_text:
            plan_payload["exclude_text"] = exclude_text
        if exclude_paths:
            plan_payload["exclude_paths"] = exclude_paths
        plan_response = await self._post(
            f"/internal/worker/tasks/{task_id}/plan",
            plan_payload,
        )
        planned_files = [
            str(item.get("path"))
            for item in plan_response.get("plan", {}).get("files", [])
            if isinstance(item, dict) and item.get("path")
        ]
        commands = plan_response.get("plan", {}).get("commands", [])
        command_receipts: list[dict[str, Any]] = []
        for command in commands:
            result = await self._post(
                f"/internal/worker/tasks/{task_id}/exec",
                {"fencing_token": fence, "command": command},
            )
            command_receipts.append({**result, "command": command})
            if result.get("exit_code") != 0:
                detail = str(result.get("stderr") or result.get("stdout") or "")[-1000:]
                raise HybridExecutionError(
                    f"[{phase_name}] terminal command {command!r} exited with code "
                    f"{result.get('exit_code')}: {detail}",
                    exit_code=int(result.get("exit_code")),
                )
        return {
            "kind": "terminal",
            "phase": phase_name,
            "commands": command_receipts,
            "files": planned_files,
            "model": plan_response.get("model"),
        }

    async def _plan_gui_phase(self, task: dict[str, Any], fence: int) -> dict[str, Any]:
        task_id = task["task_id"]
        return await self._post(
            f"/internal/worker/tasks/{task_id}/gui/plan",
            {"fencing_token": fence},
        )

    async def _run_gui_phase(
        self, task: dict[str, Any], fence: int, plan_response: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = task["task_id"]
        steps = plan_response.get("steps") or []
        expected_text = plan_response.get("expected_text") or task.get("expected_text")
        result = await self._post(
            f"/internal/worker/tasks/{task_id}/gui/execute",
            {
                "fencing_token": fence,
                "steps": steps,
                "expected_text": expected_text,
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
            # Plan the GUI phase first so the prepare phase can be told which line
            # the desktop will own and therefore must not write.
            gui_plan = await self._plan_gui_phase(task, fence)
            prepare = await self._run_terminal_phase(
                task, fence, "prepare", exclude_text=gui_plan.get("expected_text")
            )
            phases.append(prepare)
            gui_phase = await self._run_gui_phase(task, fence, gui_plan)
            phases.append(gui_phase)
            # The deliverable now exists and carries the GUI edit; the verify phase
            # may read it but must not rewrite it. Derive the protected paths from the
            # commands the prepare phase actually ran, since the plan response does not
            # echo the materialized file list.
            protected_paths = sorted(
                persisted_paths([receipt.get("command") for receipt in prepare.get("commands", [])])
            )
            verify = await self._run_terminal_phase(
                task, fence, "verify", exclude_paths=protected_paths or None
            )
            phases.append(verify)
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
        # The independent verifier expects a GUI receipt and the replayable terminal
        # verification commands at the top level (the same shape a GUI or terminal
        # worker submits), plus the ordered phase timeline for the hybrid audit trail.
        verify_commands = [receipt["command"] for receipt in verify.get("commands", [])]
        return {
            "executor": "gui-worker",
            "execution_mode": "hybrid",
            "phases": phases,
            "phase_count": len(phases),
            "capabilities_used": ["terminal", "gui"],
            "gui": gui_phase["result"],
            "planned_commands": verify_commands,
            "commands": verify.get("commands", []),
            "verification_status": "awaiting_independent_verifier",
            "passed": True,
        }
