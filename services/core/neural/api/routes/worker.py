import asyncio
import base64
import binascii
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import (
    get_audit_repo,
    get_checkpoint_manager,
    get_container_manager,
    get_container_repo,
    get_employee_repo,
    get_llm_gateway,
    get_service_heartbeat_repo,
    get_task_repo,
    require_execution_not_halted,
    require_worker_token,
)
from neural.api.schemas.requests import (
    RuntimeHeartbeatRequest,
    WorkerCheckpointRequest,
    WorkerClaimRequest,
    WorkerCommandRequest,
    WorkerFailureRequest,
    WorkerGuiPlanRequest,
    WorkerGuiStepRequest,
    WorkerLeaseRequest,
    WorkerPlanRequest,
    WorkerSubmitRequest,
)
from neural.config import get_settings
from neural.constants import (
    EmployeeStatus,
    ExecutionMode,
    mode_requires_gui,
    mode_requires_terminal,
    worker_declared_modes,
)
from neural.containers.manager import ContainerManager
from neural.db.engine import get_db_session
from neural.db.models import Project
from neural.db.repositories import (
    AuditRepository,
    ContainerRepository,
    EmployeeRepository,
    ServiceHeartbeatRepository,
    TaskRepository,
)
from neural.exceptions import (
    ContainerError,
    ModelProviderError,
    QuotaExceededError,
    SecurityViolationError,
)
from neural.execution.contracts import CapabilityPolicyEnforcer
from neural.gui_intelligence.runtime.errors import DesktopRuntimeUnavailable
from neural.gui_intelligence.runtime.planner import derive_gui_steps, expected_onscreen_text
from neural.gui_intelligence.worker import (
    GuiStepPlan,
    GuiWorkerExecutionError,
    build_gui_worker_engine,
)
from neural.llm.budget import load_budget_scope_caps
from neural.llm.gateway import LLMGateway
from neural.logger import get_logger
from neural.security.kill_switch import get_kill_switch
from neural.verification.generated_script import (
    iter_persisted_python,
    validate_generated_commands,
    validate_generated_files,
)
from neural.verification.test_commands import (
    replayable_test_commands,
    scope_test_commands_to_plan,
    validate_test_plan,
    validate_verify_phase_plan,
    validate_workspace_artifact_plan,
)
from neural.verification.workspace_manifest import valid_workspace_manifest
from neural.workers.terminal import TerminalCommandPlan, TerminalPlan
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(
    prefix="/internal/worker",
    tags=["Worker protocol"],
    dependencies=[Depends(require_worker_token), Depends(require_execution_not_halted)],
)
logger = get_logger("ancos.api.worker")

# Rejected model output is kept verbatim so an operator can see exactly what the
# model produced and which rule rejected it. The directory is host-visible under
# the mounted ./data volume; files are written best-effort and never block replan.
_GENERATION_EVIDENCE_DIR = os.path.join("data", "evidence", "generation_failures")


def _persist_generation_failure_evidence(
    *,
    task_id: str,
    phase: str,
    attempt: int,
    files: list[tuple[str, str]],
    problems: list[Any],
) -> None:
    """Preserve a rejected generated plan and the rules that rejected it."""
    try:
        safe_task = re.sub(r"[^A-Za-z0-9_.-]", "_", task_id)[:120] or "task"
        safe_phase = re.sub(r"[^A-Za-z0-9_.-]", "_", phase)[:40] or "single"
        target_dir = os.path.join(_GENERATION_EVIDENCE_DIR, safe_task)
        os.makedirs(target_dir, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        payload = {
            "task_id": task_id,
            "phase": phase,
            "planning_attempt": attempt,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "problems": [problem.render() for problem in problems],
            "files": [{"path": path, "content": content} for path, content in files],
        }
        target = os.path.join(target_dir, f"{safe_phase}_attempt{attempt}_{stamp}.json")
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning(
            "Could not persist generation-failure evidence",
            task_id=task_id,
            error=str(exc),
        )


def _without_excluded_line(content: str, excluded: str) -> str:
    """Drop ``excluded`` lines from ``content`` so a later phase owns them alone."""
    kept = [line for line in content.splitlines() if line.strip() != excluded]
    if len(kept) == len(content.splitlines()):
        return content
    return "\n".join(kept) + ("\n" if content.endswith("\n") else "")


def _ensure_single_trailing_newline(content: str) -> str:
    """End ``content`` with exactly one newline.

    A hybrid task's graphical phase appends its line by moving to the end of the
    document and typing. If the prepared artifact has no trailing newline the
    typed line concatenates onto the last line; if it has several, the append
    lands after a blank line. Exactly one trailing newline makes the append a
    clean new line in both cases.
    """
    if not content:
        return content
    return content.rstrip("\n") + "\n"


_SINGLE_CHUNK_WRITE = re.compile(
    r"^import base64; open\((?P<path>.*), (?P<mode>'wb')\)\.write\(base64\.b64decode\((?P<chunk>'[A-Za-z0-9+/=]*')\)\)$"
)


def _normalize_prepare_write_commands(commands: list[list[str]], excluded: str) -> list[list[str]]:
    """Apply the prepare-phase artifact invariant to raw base64 write commands.

    The planner asks for the artifact under ``files``, but the model sometimes
    emits the write as a ``python3 -c`` command instead. Those commands bypass the
    ``files`` normalization, so normalize them here as well: strip the later
    phase's line and end the file with exactly one newline. Only single-chunk
    writes are rewritten; a chunked (append-mode) write is left untouched so a
    partial rewrite can never corrupt a large file.
    """
    if any(mode in command for command in commands for mode in ("'ab'", '"ab"')):
        return commands
    normalized: list[list[str]] = []
    for command in commands:
        if len(command) == 3 and command[0] in {"python", "python3"} and command[1] == "-c":
            match = _SINGLE_CHUNK_WRITE.match(command[2])
            if match:
                try:
                    content = base64.b64decode(match.group("chunk")[1:-1]).decode("utf-8")
                except (ValueError, binascii.Error, UnicodeDecodeError):
                    normalized.append(command)
                    continue
                fixed = _ensure_single_trailing_newline(_without_excluded_line(content, excluded))
                encoded = base64.b64encode(fixed.encode("utf-8")).decode("ascii")
                script = (
                    f"import base64; open({match.group('path')}, 'wb')"
                    f".write(base64.b64decode({encoded!r}))"
                )
                normalized.append([command[0], "-c", script])
                continue
        normalized.append(command)
    return normalized


@router.post("/runtime/heartbeat")
async def heartbeat_worker_runtime(
    request: RuntimeHeartbeatRequest,
    worker_id: str = Depends(require_worker_token),
    heartbeat_repo: ServiceHeartbeatRepository = Depends(get_service_heartbeat_repo),
):
    receipt = await heartbeat_repo.record("worker", worker_id, request.instance_id)
    return {
        "service_type": receipt.service_type,
        "service_id": receipt.service_id,
        "instance_id": receipt.instance_id,
        "last_seen_at": receipt.last_seen_at,
    }


@router.post("/tasks/claim")
async def claim_next_task(
    request: WorkerClaimRequest = WorkerClaimRequest(),
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
    get_kill_switch().guard_active()
    task = await task_repo.claim_next_task(worker_id, execution_modes=request.execution_modes)
    if task:
        # Execution-target requirement is mode-specific: every task needs a live
        # execution environment (the employee container), while only GUI-bearing
        # modes additionally need a reachable graphical desktop. Terminal tasks
        # must not be blocked merely because no desktop is running.
        workstation = await container_manager.get_desktop_by_employee_id(
            task["employee_id"],
            container_repo=container_repo,
            employee_repo=employee_repo,
        )
        if not workstation or workstation.get("status") != "running":
            await task_repo.release_task_claim(
                task["task_id"],
                worker_id,
                task["fencing_token"],
                "Assigned employee execution environment is not live and available.",
            )
            return {"task": None, "status": "execution_environment_unavailable"}
    return {"task": task, "status": "claimed" if task else "idle"}


@router.post("/tasks/{task_id}/start")
async def start_task_attempt(
    task_id: str,
    request: WorkerLeaseRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
    worker_id: str = Depends(require_worker_token),
):
    get_kill_switch().guard_active()
    if not await task_repo.worker_claim_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    task = await task_repo.get_by_task_id(task_id)
    if task:
        declared_modes = worker_declared_modes(worker_id, get_settings().WORKER_EXECUTION_MODES)
        normalized_mode = ExecutionMode.normalize(task.execution_mode)
        if declared_modes is not None and normalized_mode not in declared_modes:
            mode_str = getattr(task.execution_mode, "value", str(task.execution_mode))
            await task_repo.release_task_claim(
                task_id,
                worker_id,
                request.fencing_token,
                f"Worker '{worker_id}' cannot execute {mode_str}-mode tasks.",
            )
            raise HTTPException(
                status_code=409,
                detail=f"Worker '{worker_id}' cannot execute {mode_str}-mode tasks.",
            )
    employee = (
        await employee_repo.get_by_employee_id(task.assigned_to)
        if task and task.assigned_to
        else None
    )
    workstation = (
        await container_manager.get_desktop_by_employee_id(
            employee.employee_id,
            container_repo=container_repo,
            employee_repo=employee_repo,
        )
        if employee
        else None
    )
    if not workstation or workstation.get("status") != "running":
        await task_repo.release_task_claim(
            task_id,
            worker_id,
            request.fencing_token,
            "Assigned employee workstation is not live at worker start.",
        )
        raise HTTPException(status_code=409, detail="Assigned employee workstation is not live.")
    try:
        workspace_manifest_before = await container_manager.capture_verifier_manifest(
            employee.employee_id
        )
    except ContainerError as exc:
        await task_repo.release_task_claim(
            task_id,
            worker_id,
            request.fencing_token,
            "Could not capture a complete task-start workspace baseline.",
        )
        raise HTTPException(
            status_code=503, detail="Task-start workspace baseline capture failed."
        ) from exc
    if not valid_workspace_manifest(workspace_manifest_before, allow_empty=True):
        await task_repo.release_task_claim(
            task_id,
            worker_id,
            request.fencing_token,
            "Task-start workspace baseline was incomplete or invalid.",
        )
        raise HTTPException(
            status_code=503, detail="Task-start workspace baseline is incomplete or invalid."
        )

    started = await task_repo.start_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
        workspace_manifest_before,
    )
    if not started:
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    return {"task_id": task_id, "status": "running", "fencing_token": request.fencing_token}


@router.post("/tasks/{task_id}/heartbeat")
async def heartbeat_task_attempt(
    task_id: str,
    request: WorkerLeaseRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    worker_id: str = Depends(require_worker_token),
):
    get_kill_switch().guard_active()
    lease_expires_at = await task_repo.heartbeat_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
    )
    if lease_expires_at is None:
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    return {"task_id": task_id, "status": "running", "lease_expires_at": lease_expires_at}


@router.post("/tasks/{task_id}/plan")
async def plan_terminal_task(
    task_id: str,
    request: WorkerPlanRequest,
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    gateway: LLMGateway = Depends(get_llm_gateway),
    session: AsyncSession = Depends(get_db_session),
):
    """Generate a terminal plan inside Core API so workers hold no provider credentials."""
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not mode_requires_terminal(task.execution_mode):
        raise HTTPException(
            status_code=409, detail="This endpoint accepts terminal-bearing tasks only."
        )
    if not await task_repo.worker_lease_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )

    resource_budget = task.resource_budget or {}
    raw_max_tokens = resource_budget.get("max_tokens", 6000)
    if (
        isinstance(raw_max_tokens, bool)
        or not isinstance(raw_max_tokens, int)
        or raw_max_tokens < 1
    ):
        raise HTTPException(status_code=409, detail="Task token budget is invalid.")
    max_tokens = min(6000, raw_max_tokens)
    project = await session.get(Project, task.project_id)
    if project is None:
        raise HTTPException(
            status_code=409, detail="Task project is missing; metered inference is blocked."
        )
    budget_scope_caps = await load_budget_scope_caps(
        session,
        gateway.cost_tracker,
        project_id=project.id,
        task_id=task.task_id,
    )
    phase = (request.phase or "").strip().lower()
    excluded = (request.exclude_text or "").strip()
    protected_paths = list(request.exclude_paths or [])
    phase_guidance = ""
    if phase == "prepare":
        phase_guidance = (
            "This run is the PREPARE phase of a hybrid task that continues on a graphical desktop "
            "and later returns to the terminal. Create and persist ONLY the initial artifact(s) this "
            "phase is responsible for, with exactly the contents specified, then stop. Do NOT attempt "
            "the later graphical step and do NOT try to verify the final form; later phases own that. "
            "Do NOT run any test command (pytest, unittest, npm test) in this phase — the final "
            "verify phase runs the tests after the graphical edit. Your `commands` may be empty; "
            "put the artifact contents in `files` and stop.\n"
        )
        if excluded:
            phase_guidance += (
                f"The literal line {excluded!r} belongs to the later graphical phase. It MUST NOT "
                "appear anywhere in the files you create or the commands you run.\n"
            )
        phase_guidance += "\n"
    elif phase == "verify":
        phase_guidance = (
            "This run is the FINAL VERIFY phase of a hybrid task. The deliverable already exists in "
            "the workspace and was finalized by an earlier graphical desktop phase. Only READ the "
            "existing artifact, assert that it contains every expected line exactly once and that the "
            "original lines are intact, and print its SHA-256 hash. "
        )
        if protected_paths:
            phase_guidance += (
                f"The artifact(s) {', '.join(repr(path) for path in protected_paths)} already exist "
                "and were finalized by the earlier graphical phase. Read them; never create, rewrite, "
                "or overwrite them.\n"
            )
        phase_guidance += (
            "Persist your checker as a pytest module named `test_<name>.py` that defines at least one "
            "`def test_...()` function (never a `__main__`-only script), and run it with "
            '`["python3","-m","pytest","-q"]` so pytest discovers and executes the test. A pytest run '
            "that collects no tests exits non-zero, so the file name and test functions must match "
            "pytest's discovery rules. Assert ONLY the properties stated in the acceptance criteria "
            "(the exact lines and their uniqueness); never hardcode an invented hash or checksum value. "
            "Compute the SHA-256 with `hashlib` and print it, but do not assert it equals a constant "
            "you made up.\n\n"
        )
    test_instruction = (
        "If acceptance requires tests to pass, `commands` must include a separate replayable test "
        "runner that reads the persisted files, and it must use the module form such as "
        '`["python3","-m","unittest","-v"]` or `["python3","-m","pytest","-q"]`; '
        'never run a test file directly as `["python3","file.py"]`. '
        if phase != "prepare"
        else "Do NOT include a pytest/unittest/npm test runner in this phase. "
    )
    base_prompt = (
        phase_guidance
        + "Produce a short, bounded plan to complete this task inside the employee workspace. "
        "Commands run directly without a shell in a disposable, non-root employee container. "
        "Do not access secrets, the host, Docker, or external services. Do not use destructive "
        "commands. Use only python, python3, pytest, npm, or npx.\n\n"
        "Return two fields:\n"
        "- `files`: every file the task asks you to create, as objects with `path` and `content`. "
        "Put the COMPLETE raw file contents here, source and tests together; never embed file "
        "contents inside a command string.\n"
        "- `commands`: the commands to run after those files exist. Return `commands` as a JSON "
        "array of argv arrays; every command must be a nested array of separate argument strings, "
        "never a shell command string. Do not put function/class definitions or test assertions "
        "directly in a command, and do not inline test definitions in the test-running command. "
        + test_instruction
        + "Commands must not claim "
        "success unless their exit codes are zero.\n\n"
        f"Title: {task.title}\nObjective: {task.objective}\nScope: {task.scope}\n"
        f"Acceptance criteria: {task.acceptance_criteria}"
    )
    system_prompt = (
        "You are a terminal-mode software engineering employee. Return only valid JSON matching the "
        "provided schema. Put complete file contents in the `files` field, not inside commands. The "
        "commands property is a list of argv arrays, not a list of shell strings. Persist requested "
        "files in the workspace and run tests against those persisted files."
    )
    plan: TerminalCommandPlan
    response: Any
    validation_error: ValueError | None = None
    try:
        for planning_attempt in range(2):
            correction = ""
            if validation_error is not None:
                if phase == "prepare":
                    correction = (
                        "\n\nYour previous plan was rejected before execution: "
                        f"{validation_error} Return a corrected plan that persists ONLY the initial "
                        "artifact under `files` and runs no test command at all."
                    )
                else:
                    correction = (
                        "\n\nYour previous plan was rejected before execution: "
                        f"{validation_error} Return a corrected plan that lists every requested "
                        "workspace file under `files` with its exact path and full contents, then runs "
                        'a separate replayable test command such as `["python3","-m","unittest","-v"]`.'
                    )
            provider_plan, response = await gateway.generate_structured(
                prompt=base_prompt + correction,
                response_model=TerminalPlan,
                route="code_generation",
                system_prompt=system_prompt,
                employee_id=task.assigned_to,
                task_id=task.task_id,
                project_id=task.project_id,
                temperature=0.1,
                max_tokens=max_tokens,
                db_session=session,
                budget_scope_caps=budget_scope_caps,
            )
            if phase == "prepare" and excluded:
                # The later graphical phase types this exact line into the desktop
                # editor, so the prepare phase must not also persist it. The model
                # sometimes ignores the prompt instruction; strip it deterministically
                # so the GUI edit is never duplicated.
                provider_plan.files = [
                    workspace_file.model_copy(
                        update={
                            "content": _ensure_single_trailing_newline(
                                _without_excluded_line(workspace_file.content, excluded)
                            )
                        }
                    )
                    for workspace_file in provider_plan.files
                ]
            script_problems = validate_generated_files(
                (workspace_file.path, workspace_file.content)
                for workspace_file in provider_plan.files
            )
            if script_problems:
                # The model wrote a script that cannot run. Preserve it verbatim
                # as diagnostic evidence, then replan with the concrete problem.
                _persist_generation_failure_evidence(
                    task_id=task.task_id,
                    phase=phase or "single",
                    attempt=planning_attempt + 1,
                    files=[
                        (workspace_file.path, workspace_file.content)
                        for workspace_file in provider_plan.files
                    ],
                    problems=script_problems,
                )
                validation_error = ValueError(
                    "Generated script is not executable: "
                    + "; ".join(problem.render() for problem in script_problems)
                )
                logger.warning(
                    "Generated script rejected before execution",
                    task_id=task.task_id,
                    planning_attempt=planning_attempt + 1,
                    problems=[problem.render() for problem in script_problems],
                )
                continue
            try:
                plan = provider_plan.materialize()
            except ValueError as exc:
                validation_error = exc
                logger.warning(
                    "Terminal plan could not be materialized",
                    task_id=task.task_id,
                    planning_attempt=planning_attempt + 1,
                    reason=str(exc),
                )
                continue
            if phase == "prepare":
                # The model sometimes persists the artifact as a raw base64 write
                # command instead of a `files` entry, bypassing the `files`
                # normalization above; normalize those commands too so the
                # single-trailing-newline invariant always holds.
                if excluded:
                    normalized_commands = _normalize_prepare_write_commands(plan.commands, excluded)
                    if normalized_commands != plan.commands:
                        plan = TerminalCommandPlan(commands=normalized_commands)
                # The graphical phase owns verification; a prepare-phase plan that
                # appends its own test runner fails closed on missing files and
                # would verify the artifact before the GUI edit. Drop those runners.
                test_runners = set(
                    map(tuple, replayable_test_commands({"planned_commands": plan.commands}))
                )
                if test_runners:
                    plan = TerminalCommandPlan(
                        commands=[c for c in plan.commands if tuple(c) not in test_runners]
                    )
            # Workspaces are reused across objectives, so a bare `pytest` would also
            # collect leftover test modules from earlier objectives. Scope it to the
            # modules this plan persists so the run is hermetic.
            scoped_commands = scope_test_commands_to_plan(plan.commands)
            if scoped_commands != plan.commands:
                plan = TerminalCommandPlan(commands=scoped_commands)
            # The model sometimes persists a Python script as a raw base64 write
            # command instead of a `files` entry. Those scripts bypass the file
            # validator above, so validate what the commands would actually write.
            command_script_problems = validate_generated_commands(plan.commands)
            if command_script_problems:
                _persist_generation_failure_evidence(
                    task_id=task.task_id,
                    phase=phase or "single",
                    attempt=planning_attempt + 1,
                    files=list(iter_persisted_python(plan.commands)),
                    problems=command_script_problems,
                )
                validation_error = ValueError(
                    "Generated script is not executable: "
                    + "; ".join(problem.render() for problem in command_script_problems)
                )
                logger.warning(
                    "Generated script in commands rejected before execution",
                    task_id=task.task_id,
                    planning_attempt=planning_attempt + 1,
                    problems=[problem.render() for problem in command_script_problems],
                )
                continue
            try:
                validate_test_plan(task.acceptance_criteria, plan.commands)
                task_context = "\n".join((task.title or "", task.objective or "", task.scope or ""))
                validate_workspace_artifact_plan(
                    task_context, task.acceptance_criteria, plan.commands
                )
                if phase == "verify":
                    validate_verify_phase_plan(protected_paths, plan.commands)
                validation_error = None
                break
            except ValueError as exc:
                validation_error = exc
                logger.warning(
                    "Terminal command plan rejected before execution",
                    task_id=task.task_id,
                    planning_attempt=planning_attempt + 1,
                    reason=str(exc),
                )
        if validation_error is not None:
            raise HTTPException(status_code=422, detail=str(validation_error))
    except QuotaExceededError as exc:
        raise HTTPException(
            status_code=429, detail="Budget reservation denied; provider was not called."
        ) from exc
    except ModelProviderError as exc:
        raise HTTPException(
            status_code=502, detail="All configured planning providers failed."
        ) from exc

    logger.info(
        "Terminal phase plan produced",
        task_id=task.task_id,
        phase=phase or "single",
        files=[workspace_file.path for workspace_file in provider_plan.files],
        commands=plan.commands,
    )
    return {
        "plan": plan.model_dump(),
        "model": {
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
        },
    }


@router.post("/tasks/{task_id}/exec")
async def execute_terminal_command(
    task_id: str,
    request: WorkerCommandRequest,
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
    get_kill_switch().guard_active()
    from neural.boss.recovery import WorkerFaultInjector

    fault = WorkerFaultInjector.check_and_trigger(task_id)
    if fault:
        raise HTTPException(
            status_code=500,
            detail=f"Injected worker fault: {fault.get('error_message', 'Worker fault')}",
        )
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not mode_requires_terminal(task.execution_mode):
        raise HTTPException(
            status_code=409,
            detail="This worker execution endpoint accepts terminal-bearing tasks only.",
        )
    try:
        CapabilityPolicyEnforcer.validate_action(
            task.execution_mode,
            "terminal_command",
            {"command": request.command},
        )
    except SecurityViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not await task_repo.worker_lease_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    employee = (
        await employee_repo.get_by_employee_id(task.assigned_to) if task.assigned_to else None
    )
    if not employee:
        raise HTTPException(status_code=409, detail="Task has no assigned employee workspace.")
    workstation = await container_manager.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    if not workstation or workstation.get("status") != "running":
        raise HTTPException(status_code=409, detail="Assigned employee workstation is not live.")
    result = await container_manager.execute_command(
        workstation["docker_id"], request.command, workdir="/home/employee/workspace"
    )
    return {
        "task_id": task_id,
        "fencing_token": request.fencing_token,
        "exit_code": result.get("exit_code"),
        "stdout": str(result.get("stdout", ""))[-16000:],
        "stderr": str(result.get("stderr", ""))[-4000:],
    }


@router.post("/tasks/{task_id}/gui/plan")
async def plan_gui_task(
    task_id: str,
    request: WorkerLeaseRequest,
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
):
    """Generate concrete GUI steps for a task from its objective.

    Steps reference on-screen text so the live-desktop OCR grounder can locate
    them. This is planning only; nothing is dispatched to the screen.
    """
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not mode_requires_gui(task.execution_mode):
        raise HTTPException(status_code=409, detail="This endpoint accepts GUI-bearing tasks only.")
    if not await task_repo.worker_lease_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    steps = derive_gui_steps(
        task.title or "",
        task.objective or "",
        task.scope or "",
        task.acceptance_criteria or "",
    )
    return {
        "task_id": task_id,
        "fencing_token": request.fencing_token,
        "steps": steps,
        "expected_text": expected_onscreen_text(steps),
    }


@router.post("/tasks/{task_id}/gui/execute")
async def execute_gui_plan(
    task_id: str,
    request: WorkerGuiPlanRequest,
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
    """Execute a GUI plan against the employee's live graphical desktop.

    The desktop lives inside the employee container; the core API orchestrates by
    running scrot/xdotool there through the restricted broker. Terminal-only tasks
    are rejected here; GUI and hybrid tasks are accepted.
    """
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not mode_requires_gui(task.execution_mode):
        raise HTTPException(status_code=409, detail="This endpoint accepts GUI-bearing tasks only.")
    try:
        CapabilityPolicyEnforcer.validate_action(
            task.execution_mode, "gui_action", {"steps": len(request.steps)}
        )
    except SecurityViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not await task_repo.worker_lease_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    employee = (
        await employee_repo.get_by_employee_id(task.assigned_to) if task.assigned_to else None
    )
    if not employee:
        raise HTTPException(status_code=409, detail="Task has no assigned employee workspace.")
    workstation = await container_manager.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    if not workstation or workstation.get("status") != "running":
        raise HTTPException(status_code=409, detail="Assigned employee workstation is not live.")

    steps = [GuiStepPlan(**step.model_dump()) for step in request.steps]
    try:
        engine = build_gui_worker_engine(container_manager, display=request.display or ":1")
        result = await asyncio.to_thread(
            engine.execute,
            employee_id=employee.employee_id,
            docker_id=workstation["docker_id"],
            steps=steps,
            expected_text=request.expected_text,
            expected_postcondition=request.expected_postcondition,
        )
    except DesktopRuntimeUnavailable as exc:
        raise HTTPException(status_code=409, detail=f"Live desktop unavailable: {exc}") from exc
    except ContainerError as exc:
        # The workstation can disappear between the liveness check above and the
        # first broker call (lease reaper, operator terminate, daemon restart). That
        # is a retryable capacity condition, not a server fault, so surface it as a
        # conflict the worker can re-claim against instead of an unhandled 500.
        raise HTTPException(
            status_code=409, detail=f"Live desktop became unavailable mid-task: {exc}"
        ) from exc
    except GuiWorkerExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"task_id": task_id, "fencing_token": request.fencing_token, "result": result.to_dict()}


@router.post("/tasks/{task_id}/gui/step")
async def execute_gui_step(
    task_id: str,
    request: WorkerGuiStepRequest,
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    container_repo: ContainerRepository = Depends(get_container_repo),
    container_manager: ContainerManager = Depends(get_container_manager),
):
    """Execute a single GUI step against the live desktop (per-step control)."""
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not mode_requires_gui(task.execution_mode):
        raise HTTPException(status_code=409, detail="This endpoint accepts GUI-bearing tasks only.")
    if not await task_repo.worker_lease_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    employee = (
        await employee_repo.get_by_employee_id(task.assigned_to) if task.assigned_to else None
    )
    if not employee:
        raise HTTPException(status_code=409, detail="Task has no assigned employee workspace.")
    workstation = await container_manager.get_desktop_by_employee_id(
        employee.employee_id,
        container_repo=container_repo,
        employee_repo=employee_repo,
    )
    if not workstation or workstation.get("status") != "running":
        raise HTTPException(status_code=409, detail="Assigned employee workstation is not live.")
    try:
        engine = build_gui_worker_engine(container_manager, display=request.display or ":1")
        result = await asyncio.to_thread(
            engine.execute,
            employee_id=employee.employee_id,
            docker_id=workstation["docker_id"],
            steps=[GuiStepPlan(**request.step.model_dump())],
        )
    except DesktopRuntimeUnavailable as exc:
        raise HTTPException(status_code=409, detail=f"Live desktop unavailable: {exc}") from exc
    except ContainerError as exc:
        raise HTTPException(
            status_code=409, detail=f"Live desktop became unavailable mid-step: {exc}"
        ) from exc
    except GuiWorkerExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"task_id": task_id, "fencing_token": request.fencing_token, "result": result.to_dict()}


@router.post("/tasks/{task_id}/checkpoint")
async def record_task_checkpoint(
    task_id: str,
    request: WorkerCheckpointRequest,
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    checkpoint_manager=Depends(get_checkpoint_manager),
):
    """Persist the ordered phases a hybrid task has durably completed.

    Written after each phase so a mid-task desktop disappearance can resume from
    the last completed phase instead of replaying GUI side effects that already
    happened. The checkpoint is bound to the task's current fencing token, so a
    stale worker cannot resurrect a superseded attempt.
    """
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not await task_repo.worker_lease_is_current(task_id, worker_id, request.fencing_token):
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    if not task.assigned_to:
        raise HTTPException(status_code=409, detail="Task has no assigned employee workspace.")
    checkpoint_manager.save_checkpoint(
        employee_id=task.assigned_to,
        task_id=task.task_id,
        step_index=request.step_index,
        state_dict={
            "phase": request.phase,
            "completed_phases": list(request.completed_phases),
            "protected_paths": list(request.protected_paths),
            "prepare_plan": [list(command) for command in request.prepare_plan],
            "fencing_token": request.fencing_token,
        },
    )
    return {
        "task_id": task_id,
        "phase": request.phase,
        "completed_phases": list(request.completed_phases),
        "fencing_token": request.fencing_token,
    }


@router.get("/tasks/{task_id}/checkpoint")
async def read_task_checkpoint(
    task_id: str,
    fencing_token: int,
    worker_id: str = Depends(require_worker_token),
    task_repo: TaskRepository = Depends(get_task_repo),
    checkpoint_manager=Depends(get_checkpoint_manager),
):
    """Return the durable checkpoint for a task, or null when none is usable.

    The reading worker must hold the task's current lease (stale workers are
    rejected above). A checkpoint written by an *earlier* attempt of the same task
    is still returned: its GUI side effects live in the durable workspace, so
    resuming from it prevents replaying them. A checkpoint whose recorded token is
    ahead of the task's current token cannot exist for this lineage and is ignored.
    """
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not await task_repo.worker_lease_is_current(task_id, worker_id, fencing_token):
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    if not task.assigned_to:
        raise HTTPException(status_code=409, detail="Task has no assigned employee workspace.")
    checkpoint = checkpoint_manager.load_checkpoint(task.assigned_to, task.task_id)
    state = (checkpoint or {}).get("state_dict") or {}
    recorded_fence = state.get("fencing_token")
    if (
        not checkpoint
        or not isinstance(recorded_fence, int)
        or recorded_fence > task.fencing_token
    ):
        return {"task_id": task_id, "checkpoint": None}
    return {
        "task_id": task_id,
        "fencing_token": fencing_token,
        "checkpoint": {
            "phase": state.get("phase"),
            "completed_phases": list(state.get("completed_phases") or []),
            "protected_paths": list(state.get("protected_paths") or []),
            "prepare_plan": [list(command) for command in (state.get("prepare_plan") or [])],
            "step_index": checkpoint.get("step_index", 0),
            "saved_at": checkpoint.get("saved_at"),
        },
    }


@router.post("/tasks/{task_id}/submit")
async def submit_task_attempt(
    task_id: str,
    request: WorkerSubmitRequest,
    task_repo: TaskRepository = Depends(get_task_repo),
    worker_id: str = Depends(require_worker_token),
):
    get_kill_switch().guard_active()
    submitted = await task_repo.submit_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
        request.execution_receipt,
    )
    if not submitted:
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    return {
        "task_id": task_id,
        "status": "submitted_for_verification",
        "task_status": "running",
        "fencing_token": request.fencing_token,
    }


@router.post("/tasks/{task_id}/fail")
async def fail_task_attempt(
    task_id: str,
    request: WorkerFailureRequest,
    audit_repo: AuditRepository = Depends(get_audit_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    worker_id: str = Depends(require_worker_token),
):
    get_kill_switch().guard_active()
    task = await task_repo.get_by_task_id(task_id)
    failure = await task_repo.fail_task_attempt(
        task_id,
        worker_id,
        request.fencing_token,
        request.error_message,
        exit_code=request.exit_code,
    )
    if failure is None:
        raise HTTPException(
            status_code=409, detail="Worker lease expired or fencing token was superseded."
        )
    if task and task.assigned_to and not failure["retrying"]:
        await employee_repo.update_status(task.assigned_to, EmployeeStatus.IDLE)
    await audit_repo.log(
        actor="BossAI",
        action="TASK_FAILURE_RECOVERY",
        resource_type="Task",
        resource_id=task_id,
        details={
            "retrying": failure["retrying"],
            "retry_count": failure["retry_count"],
            "retry_after_seconds": failure["retry_after_seconds"],
            "reassigned": failure["reassigned"],
            "previous_employee_id": failure["previous_employee_id"],
            "assigned_employee_id": failure["assigned_employee_id"],
        },
    )
    return {
        "task_id": task_id,
        "status": "retrying" if failure["retrying"] else "failed",
        "retry_count": failure["retry_count"],
        "retry_after_seconds": failure["retry_after_seconds"],
        "fencing_token": request.fencing_token,
        "reassigned": failure["reassigned"],
        "assigned_employee_id": failure["assigned_employee_id"],
    }
