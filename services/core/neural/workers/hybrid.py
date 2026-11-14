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


class HybridInfrastructureError(HybridExecutionError):
    """Raised when the execution environment (desktop/container) is unavailable.

    The task's own work never got a fair chance, so this must be reported as an
    infrastructure failure: the task is requeued without consuming its retry
    budget, and the GUI phase is never repeated blindly against a dead desktop.
    """


# A desktop that vanishes mid-task surfaces as a retryable 409 from the
# GUI-execution route ("Live desktop became unavailable mid-task"), and a desktop
# that is already gone surfaces as 409/503 too. Both mean the environment failed,
# not the task.
_INFRASTRUCTURE_STATUS_CODES = frozenset({409, 503})


def _is_infrastructure_failure(exc: BaseException) -> bool:
    """True when the failure is an unavailable execution environment.

    Reuses the Boss recovery classifier's marker set so the worker and the
    control plane agree on what counts as infrastructure, and additionally treats
    the GUI route's retryable 409/503 as environmental.
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _INFRASTRUCTURE_STATUS_CODES:
        return True
    from neural.boss.recovery import is_infrastructure_failure

    return is_infrastructure_failure(exc)


class HybridExecutionCoordinator:
    def __init__(self, worker: Any) -> None:
        self.worker = worker

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.worker._post(path, payload)

    async def _record_checkpoint(
        self,
        task: dict[str, Any],
        fence: int,
        phase: str,
        completed_phases: list[str],
        protected_paths: list[str] | None = None,
        prepare_plan: list[list[str]] | None = None,
    ) -> None:
        """Persist completed phases and the deterministic prepare plan.

        The prepare plan is recorded so a retry can re-apply the *same* artifact
        reset instead of replanning it. That matters because the GUI phase edits
        the artifact by appending: a retry that skipped the reset would append the
        line a second time and corrupt the deliverable.

        A checkpoint write must never fail the task: if the environment already
        vanished the write itself may error, and the coordinator still needs the
        real failure to surface.
        """
        try:
            await self._post(
                f"/internal/worker/tasks/{task['task_id']}/checkpoint",
                {
                    "fencing_token": fence,
                    "phase": phase,
                    "completed_phases": list(completed_phases),
                    "protected_paths": list(protected_paths or []),
                    "prepare_plan": [list(command) for command in (prepare_plan or [])],
                    "step_index": len(completed_phases),
                },
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Could not persist hybrid checkpoint",
                task_id=task.get("task_id"),
                phase=phase,
                error=str(exc),
            )

    async def _load_checkpoint(self, task: dict[str, Any], fence: int) -> dict[str, Any]:
        """Return the prior attempt's durable checkpoint, or an empty dict."""
        try:
            response = await self.worker._get(
                f"/internal/worker/tasks/{task['task_id']}/checkpoint",
                {"fencing_token": fence},
            )
        except httpx.HTTPError:
            return {}
        checkpoint = response.get("checkpoint") if isinstance(response, dict) else None
        return checkpoint if isinstance(checkpoint, dict) else {}

    async def _run_terminal_phase(
        self,
        task: dict[str, Any],
        fence: int,
        phase_name: str,
        exclude_text: str | None = None,
        exclude_paths: list[str] | None = None,
        recorded_commands: list[list[str]] | None = None,
    ) -> dict[str, Any]:
        task_id = task["task_id"]
        plan_response: dict[str, Any] = {}
        if recorded_commands is not None:
            # Replay the plan a prior attempt already produced and checkpointed.
            # Re-running the model here would yield different content for the same
            # phase and could rewrite an artifact the GUI phase already edited.
            commands = recorded_commands
            planned_files: list[str] = []
        else:
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
        try:
            result = await self._post(
                f"/internal/worker/tasks/{task_id}/gui/execute",
                {
                    "fencing_token": fence,
                    "steps": steps,
                    "expected_text": expected_text,
                    "display": getattr(self.worker, "display", None),
                },
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in _INFRASTRUCTURE_STATUS_CODES:
                raise HybridInfrastructureError(
                    f"[gui] live desktop unavailable: {exc.response.text[:400]}"
                ) from exc
            raise
        gui_result = result.get("result", {})
        if gui_result.get("status") != "success":
            raise HybridExecutionError(
                f"[gui] desktop interaction failed: {gui_result.get('error') or 'no visual change verified'}"
            )
        return {"kind": "gui", "phase": "interaction", "steps": steps, "result": gui_result}

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        fence = int(task["fencing_token"])
        phases: list[dict[str, Any]] = []
        # A prior attempt of this same task may have durably completed phases. Its
        # GUI side effects are in the persistent workspace, so they are NOT replayed.
        prior = await self._load_checkpoint(task, fence)
        completed = [
            str(phase) for phase in (prior.get("completed_phases") or []) if isinstance(phase, str)
        ]
        protected_paths = [str(path) for path in (prior.get("protected_paths") or [])]
        recorded_prepare = [
            [str(part) for part in command]
            for command in (prior.get("prepare_plan") or [])
            if isinstance(command, list) and command
        ]
        try:
            # Plan the GUI phase first so the prepare phase can be told which line
            # the desktop will own and therefore must not write.
            gui_plan = await self._plan_gui_phase(task, fence)
            # The prepare phase writes the artifact from scratch. It is re-run on
            # every attempt (not skipped) because the GUI phase edits the artifact
            # by *appending*: without a deterministic reset the retry would append
            # the GUI line a second time and corrupt the deliverable. When a prior
            # attempt recorded its prepare plan, that exact plan is replayed so the
            # reset is byte-identical instead of a fresh (possibly different) plan.
            prepare = await self._run_terminal_phase(
                task,
                fence,
                "prepare",
                exclude_text=gui_plan.get("expected_text"),
                recorded_commands=recorded_prepare or None,
            )
            phases.append(prepare)
            if "prepare" not in completed:
                completed.append("prepare")
            # The deliverable now exists and carries the GUI edit; the verify phase
            # may read it but must not rewrite it. Derive the protected paths from
            # the commands the prepare phase actually ran, and keep any recorded by
            # a prior attempt so protection never silently shrinks on a retry.
            protected_paths = sorted(
                set(protected_paths)
                | persisted_paths(
                    [receipt.get("command") for receipt in prepare.get("commands", [])]
                )
            )
            prepare_plan = [
                list(receipt.get("command")) for receipt in prepare.get("commands", [])
            ]
            await self._record_checkpoint(
                task, fence, "prepare", completed, protected_paths, prepare_plan
            )
            gui_phase = await self._run_gui_phase(task, fence, gui_plan)
            phases.append(gui_phase)
            if "gui" not in completed:
                completed.append("gui")
            await self._record_checkpoint(
                task, fence, "gui", completed, protected_paths, prepare_plan
            )
            verify = await self._run_terminal_phase(
                task, fence, "verify", exclude_paths=protected_paths or None
            )
            phases.append(verify)
            if "verify" not in completed:
                completed.append("verify")
            await self._record_checkpoint(
                task, fence, "verify", completed, protected_paths, prepare_plan
            )
        except (HybridExecutionError, httpx.HTTPError) as exc:
            infra = _is_infrastructure_failure(exc)
            logger.error(
                "Hybrid task failed",
                task_id=task.get("task_id"),
                error=str(exc),
                infrastructure=infra,
            )
            return {
                "executor": "gui-worker",
                "execution_mode": "hybrid",
                "phases": phases,
                "error": str(exc),
                # An unavailable desktop is an environment failure, not a failed
                # task. Report it with the marker the retry classifier keys on so
                # the task is requeued without charging its retry budget.
                "infrastructure_failure": infra,
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
