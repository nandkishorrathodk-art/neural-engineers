import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from neural.boss.delegator import TaskDelegator
from neural.boss.planner import TaskPlanner
from neural.boss.recovery import FailureRecoveryManager
from neural.constants import EmployeeStatus, ExecutionMode, TaskPriority, TaskStatus
from neural.db.models import Project, Task
from neural.db.repositories import (
    EmployeeRepository,
    ProjectRepository,
    ServiceHeartbeatRepository,
    TaskRepository,
)
from neural.llm.gateway import LLMGateway
from neural.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("ancos.boss")


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class BossBrain:
    """
    Central Executive Officer (CEO) and Chief Architect of ANCOS.
    Owns objective decomposition, workforce management, meeting moderation,
    DAG scheduling, and deliverable acceptance.
    """

    def __init__(
        self,
        project_repo: Optional[ProjectRepository] = None,
        task_repo: Optional[TaskRepository] = None,
        employee_repo: Optional[EmployeeRepository] = None,
        planner: Optional[TaskPlanner] = None,
        scheduler: Optional[Any] = None,
        delegator: Optional[TaskDelegator] = None,
        verifier: Optional[Any] = None,
        recovery_manager: Optional[FailureRecoveryManager] = None,
        llm_gateway: Optional[LLMGateway] = None,
        message_bus: Optional[Any] = None,
        service_heartbeat_repo: Optional[ServiceHeartbeatRepository] = None,
    ):
        self.llm_gateway = llm_gateway or LLMGateway()
        self.planner = planner or TaskPlanner(llm_gateway=self.llm_gateway)
        self.project_repo = project_repo
        self.task_repo = task_repo
        self.employee_repo = employee_repo
        self.service_heartbeat_repo = service_heartbeat_repo
        self.message_bus = message_bus
        self.delegator = delegator or TaskDelegator()
        self.verifier = verifier
        self.recovery_manager = recovery_manager or FailureRecoveryManager()
        if scheduler is not None:
            self.scheduler = scheduler
        elif task_repo and employee_repo:
            from neural.tasks.scheduler import TaskScheduler
            self.scheduler = TaskScheduler(
                task_repo=task_repo,
                employee_repo=employee_repo,
                delegator=self.delegator,
                verifier=self.verifier,
                recovery_manager=self.recovery_manager,
                message_bus=message_bus
            )
        else:
            self.scheduler = None

        self._is_paused: bool = False
        try:
            from neural.security.kill_switch import get_kill_switch
            get_kill_switch().register_boss_brain(self)
        except Exception:
            pass

    def pause(self) -> None:
        self._is_paused = True
        logger.warning("BossBrain paused by emergency halt")

    def resume(self) -> None:
        self._is_paused = False
        logger.info("BossBrain resumed from emergency halt")

    def get_scheduler(self) -> Any:

        """Returns or creates the TaskScheduler instance."""
        if not self.scheduler:
            from neural.tasks.scheduler import TaskScheduler
            self.scheduler = TaskScheduler(
                task_repo=self.task_repo,
                employee_repo=self.employee_repo,
                delegator=self.delegator,
                verifier=self.verifier,
                recovery_manager=self.recovery_manager,
                message_bus=self.message_bus
            )
        return self.scheduler

    async def analyze_and_plan(
        self, objective: str, db_session: Optional[Any] = None
    ) -> dict[str, Any]:
        from neural.exceptions import ClarificationRequiredError, EmergencyStopTriggered
        from neural.security.kill_switch import get_kill_switch

        get_kill_switch().guard_active()
        if self._is_paused:
            raise EmergencyStopTriggered("BossBrain is currently paused due to emergency halt.")

        logger.info("Boss AI analyzing incoming objective", objective=objective)
        plan = await self.planner.decompose_objective(objective, db_session=db_session)
        if plan.get("clarification_required") is True:
            questions = plan.get("clarifying_questions")
            if not isinstance(questions, list) or not questions:
                raise ValueError("Boss plan requested clarification without any specific questions.")
            raise ClarificationRequiredError([str(question) for question in questions[:3]])
        logger.info("Decomposition complete", phases_count=len(plan.get("phases", [])))
        return plan

    async def plan_and_create_project(
        self,
        objective: str,
        project_repo: Optional[ProjectRepository] = None,
        task_repo: Optional[TaskRepository] = None,
        employee_repo: Optional[EmployeeRepository] = None,
        project_name: Optional[str] = None,
        auto_assign_first_task: bool = True,
        auto_recruit_workforce: bool = False,
        deadline_days: Optional[int] = None,
        scheduled_at: Optional[datetime] = None,
    ) -> tuple[Project, list[Task]]:
        """
        Analyzes objective, produces structured execution plan,
        persists project and tasks with DAG dependencies in database via repositories,
        and assigns the first task if an employee is available.
        """
        p_repo = project_repo or self.project_repo
        t_repo = task_repo or self.task_repo
        e_repo = employee_repo or self.employee_repo

        if not p_repo:
            raise ValueError("ProjectRepository is required to persist project")

        plan = await self.analyze_and_plan(
            objective, db_session=getattr(p_repo, "session", None)
        )
        project_deadline: Optional[datetime] = None
        if deadline_days is not None:
            if deadline_days < 1 or deadline_days > 3650:
                raise ValueError("deadline_days must be between 1 and 3650")
            project_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=deadline_days)
            plan["owner_constraints"] = {
                **_json_object(plan.get("owner_constraints")),
                "deadline_days": deadline_days,
                "deadline_at": project_deadline.isoformat(),
            }
        if scheduled_at is not None:
            if scheduled_at.tzinfo is not None:
                scheduled_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
            if scheduled_at <= datetime.now(timezone.utc).replace(tzinfo=None):
                raise ValueError("scheduled_at must be in the future")
            plan["owner_constraints"] = {
                **_json_object(plan.get("owner_constraints")),
                "scheduled_at": scheduled_at.isoformat(),
            }
        name = project_name or f"Project-{plan.get('code_name', 'Alpha')}"

        project = await p_repo.create(
            name=name,
            objective=objective,
            execution_plan=plan
        )
        logger.info("Project persisted in database", project_id=project.id, name=project.name)

        created_tasks: list[Task] = []
        phase_to_task_ids: dict[str, list[str]] = {}
        task_info_dependencies: list[tuple[Task, list[str]]] = []

        if t_repo:
            phases = plan.get("phases", [])
            for phase_idx, phase in enumerate(phases):
                phase_id = phase.get("phase_id", f"PHASE-{phase_idx+1:02d}")
                phase_title = phase.get("title", f"Phase {phase_idx+1}")
                priority = TaskPriority.HIGH if phase_idx == 0 else TaskPriority.MEDIUM
                phase_to_task_ids[phase_id] = []

                for task_info in phase.get("tasks", []):
                    raw_tid = task_info.get("task_id", f"TASK-{phase_id}-{len(created_tasks)+1:02d}")
                    task = await t_repo.create(
                        task_id=raw_tid,
                        project_id=project.id,
                        title=task_info.get("title", "Untitled Task"),
                        objective=task_info.get("objective", task_info.get("title", objective)),
                        scope=task_info.get("scope", phase_title),
                        acceptance_criteria=task_info.get("acceptance_criteria", "Verification passing with clean receipts."),
                        execution_mode=ExecutionMode(task_info.get("execution_mode", ExecutionMode.TERMINAL)),
                        required_capabilities=task_info.get("skills_required", []),
                        priority=priority,
                        status=TaskStatus.PENDING,
                        resource_budget=task_info.get("resource_budget", {"estimated_hours": 4, "max_tokens": 50000}),
                        deadline=project_deadline,
                        scheduled_at=scheduled_at,
                    )
                    created_tasks.append(task)
                    phase_to_task_ids[phase_id].append(task.task_id)

                    deps = task_info.get("dependencies", [])
                    if deps:
                        task_info_dependencies.append((task, deps))

            # Persist DAG dependencies in TaskDependency table
            for task, deps in task_info_dependencies:
                for dep in deps:
                    if dep in phase_to_task_ids:
                        for prereq_tid in phase_to_task_ids[dep]:
                            if prereq_tid != task.task_id:
                                await t_repo.add_dependency(
                                    task_id=task.task_id,
                                    depends_on_task_id=prereq_tid
                                )
                    else:
                        await t_repo.add_dependency(
                            task_id=task.task_id,
                            depends_on_task_id=dep
                        )

            # Auto-assign first task if requested
            if auto_assign_first_task and created_tasks and e_repo:
                employees = await e_repo.list_all()
                skill_gaps = self._get_verified_skill_gaps(plan, list(employees))
                if not any(employee.status == EmployeeStatus.IDLE for employee in employees):
                    first_task_info = next(
                        (
                            task
                            for phase in plan.get("phases", [])
                            for task in phase.get("tasks", [])
                        ),
                        None,
                    )
                    if first_task_info:
                        role_id = str(first_task_info.get("role_required") or "software-engineer")
                        skills = [
                            str(skill).strip()
                            for skill in first_task_info.get("skills_required", [])
                            if str(skill).strip()
                        ] or ["Software Engineering"]
                        current = skill_gaps.setdefault(role_id, [])
                        for skill in skills:
                            if all(existing.casefold() != skill.casefold() for existing in current):
                                current.append(skill)
                if auto_recruit_workforce and skill_gaps:
                    from neural.employees.factory import EmployeeFactory
                    factory = EmployeeFactory(e_repo)
                    recruited = await factory.recruit_workforce_for_plan(
                        plan,
                        skill_gaps=skill_gaps,
                    )
                    employees = [*employees, *recruited]

                if employees:
                    first_task = created_tasks[0]
                    # Prioritize employee with empirically verified matching skills
                    assignee = self.delegator.select_best_assignee(first_task, employees)
                    if assignee:
                        await t_repo.assign_task(first_task.task_id, assignee.id)
                        first_task.assigned_to = assignee.id
                        first_task.status = TaskStatus.ASSIGNED
                        logger.info(
                            "Assigned first task to employee",
                            task_id=first_task.task_id,
                            employee_id=assignee.employee_id,
                            assignee_name=assignee.name
                        )

        # Notify via Message Bus if present
        if self.message_bus:
            try:
                await self.message_bus.broadcast(
                    title=f"New Mission Initialized: {project.name}",
                    content=f"Boss AI planned objective '{objective}' with {len(created_tasks)} DAG tasks across {len(plan.get('phases', []))} phases."
                )
            except Exception as e:
                logger.warning("Could not emit broadcast for new project", error=str(e))

        return project, created_tasks

    @staticmethod
    def _get_verified_skill_gaps(
        plan: dict[str, Any],
        employees: list[Any],
    ) -> dict[str, list[str]]:
        """Return role-grouped skills no current employee has empirically verified."""
        verified_skills = {
            skill.skill_name.strip().casefold()
            for employee in employees
            for skill in (employee.skills or [])
            if skill.verified and skill.skill_name.strip()
        }
        skill_gaps: dict[str, list[str]] = {}
        for phase in plan.get("phases", []):
            for task in phase.get("tasks", []):
                role_id = str(task.get("role_required") or "software-engineer").strip()
                role_gaps = skill_gaps.setdefault(role_id, [])
                for raw_skill in task.get("skills_required", []):
                    skill = str(raw_skill).strip()
                    if skill and skill.casefold() not in verified_skills:
                        if all(existing.casefold() != skill.casefold() for existing in role_gaps):
                            role_gaps.append(skill)
        return {role_id: skills for role_id, skills in skill_gaps.items() if skills}

    async def execute_project_dag(
        self,
        project_id: str,
        worker_func: Optional[Callable[[Task], Any]] = None
    ) -> dict[str, Any]:
        """
        Executes all tasks in a project following topological DAG dependencies
        and parallel dispatch limits.
        """
        sched = self.get_scheduler()
        return await sched.execute_dag(project_id=project_id, worker_func=worker_func)

    async def build_project_report(self, project_id: str) -> Optional[dict[str, Any]]:
        """Build an owner report solely from persisted project/task/verifier state."""
        if not self.project_repo or not self.task_repo:
            raise ValueError("ProjectRepository and TaskRepository are required for a project report")

        project = await self.project_repo.get_by_id(project_id)
        if not project:
            return None

        employees = await self.employee_repo.list_all() if self.employee_repo else []
        employees_by_id = {
            identity: employee
            for employee in employees
            for identity in (employee.id, employee.employee_id)
        }
        tasks = await self.task_repo.list_by_project(project.id)
        attempts = await self.task_repo.list_attempts_by_project(project.id)
        attempts_by_task: dict[str, list[dict[str, Any]]] = {}
        for attempt in attempts:
            execution = _json_object(attempt.execution_receipt)
            model = _json_object(execution.get("model"))
            receipt = _json_object(attempt.verification_receipt)
            safe_model: dict[str, Any] = {}
            for key in ("provider", "model"):
                value = model.get(key)
                if isinstance(value, str) and 0 < len(value) <= 128:
                    safe_model[key] = value
            for key in ("input_tokens", "output_tokens"):
                value = model.get(key)
                if type(value) is int and 0 <= value <= 10_000_000:
                    safe_model[key] = value
            model_cost = model.get("cost_usd")
            if isinstance(model_cost, (int, float)) and not isinstance(model_cost, bool) and 0 <= model_cost <= 1_000_000:
                safe_model["cost_usd"] = round(float(model_cost), 8)
            attempts_by_task.setdefault(attempt.task_id, []).append({
                "attempt_id": attempt.id,
                "attempt_number": attempt.attempt_number,
                "worker_id": attempt.worker_id,
                "status": attempt.status,
                "fencing_token": attempt.fencing_token,
                "lease_expires_at": attempt.lease_expires_at,
                "heartbeat_at": attempt.heartbeat_at,
                "started_at": attempt.started_at,
                "finished_at": attempt.finished_at,
                "error_message": attempt.error_message,
                "verifier_id": attempt.verified_by,
                "verification_passed": receipt.get("passed"),
                "verification_notes": receipt.get("notes"),
                "execution_model": safe_model,
            })
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        counts = {
            "planned": 0,
            "assigned": 0,
            "started": 0,
            "retrying": 0,
            "blocked": 0,
            "failed": 0,
            "completed": 0,
            "verified": 0,
            "completed_without_verification": 0,
            "cancelled": 0,
            "overdue": 0,
            "failed_attempts": 0,
            "rejected_attempts": 0,
        }
        counts["failed_attempts"] = sum(
            1 for attempt in attempts if attempt.status == "failed"
        )
        counts["rejected_attempts"] = sum(
            1 for attempt in attempts if attempt.status == "rejected"
        )
        task_reports: list[dict[str, Any]] = []

        for task in sorted(tasks, key=lambda row: row.task_id):
            status = task.status.value if hasattr(task.status, "value") else str(task.status)
            receipt = _json_object(task.verification_result)
            verified = status == TaskStatus.COMPLETED.value and receipt.get("passed") is True
            deadline = task.deadline
            if deadline and deadline.tzinfo:
                deadline = deadline.astimezone(timezone.utc).replace(tzinfo=None)
            overdue = bool(
                deadline
                and deadline < now
                and status not in {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                }
            )

            if status == TaskStatus.PENDING.value:
                counts["planned"] += 1
            elif status == TaskStatus.ASSIGNED.value:
                counts["assigned"] += 1
                if task.retry_count > 0:
                    counts["retrying"] += 1
            elif status == TaskStatus.RUNNING.value:
                counts["started"] += 1
            elif status == TaskStatus.RETRYING.value:
                counts["retrying"] += 1
            elif status == TaskStatus.BLOCKED.value:
                counts["blocked"] += 1
            elif status == TaskStatus.FAILED.value:
                counts["failed"] += 1
            elif status == TaskStatus.COMPLETED.value:
                counts["completed"] += 1
                if verified:
                    counts["verified"] += 1
                else:
                    counts["completed_without_verification"] += 1
            elif status == TaskStatus.CANCELLED.value:
                counts["cancelled"] += 1
            if overdue:
                counts["overdue"] += 1

            employee = employees_by_id.get(task.assigned_to) if task.assigned_to else None
            task_attempts = attempts_by_task.get(task.id, [])
            verification_evidence: dict[str, Any] = {}
            if receipt:
                summary = _json_object(receipt.get("test_summary"))
                test_runs = _json_list(summary.get("independent_test_runs"))
                before = _json_object(summary.get("workspace_manifest_before"))
                manifest = _json_object(summary.get("workspace_manifest"))
                delta = _json_object(summary.get("workspace_delta"))

                def safe_manifest_files(payload: dict[str, Any]) -> list[dict[str, Any]]:
                    files = _json_list(payload.get("files"))
                    safe_files: list[dict[str, Any]] = []
                    for item in files:
                        if not isinstance(item, dict):
                            continue
                        path = item.get("path")
                        size = item.get("size_bytes")
                        digest = item.get("sha256")
                        if (
                            not isinstance(path, str)
                            or not path
                            or len(path) > 256
                            or path.startswith("/")
                            or "\\" in path
                            or any(part in {"", ".", ".."} for part in path.split("/"))
                            or type(size) is not int
                            or size < 0
                            or not isinstance(digest, str)
                            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                        ):
                            continue
                        safe_files.append({"path": path, "size_bytes": size, "sha256": digest})
                    return safe_files[:32]

                safe_runs = []
                for run in test_runs[:12]:
                    if not isinstance(run, dict):
                        continue
                    command = _json_list(run.get("command"))
                    executable = command[0] if command else None
                    if not isinstance(executable, str) or executable not in {"python", "python3", "pytest", "npm"}:
                        continue
                    worker_exit_code = run.get("worker_exit_code")
                    verifier_exit_code = run.get("verifier_exit_code")
                    evidence_ref = run.get("evidence_ref")
                    safe_runs.append({
                        "executable": executable,
                        "worker_exit_code": worker_exit_code if type(worker_exit_code) is int and -255 <= worker_exit_code <= 255 else None,
                        "verifier_exit_code": verifier_exit_code if type(verifier_exit_code) is int and -255 <= verifier_exit_code <= 255 else None,
                        "evidence_ref": evidence_ref[:256] if isinstance(evidence_ref, str) else None,
                    })
                safe_manifest = safe_manifest_files(manifest)
                raw_counts = _json_object(delta.get("counts"))
                safe_counts: dict[str, int] = {}
                for key in ("created", "modified", "deleted"):
                    value = raw_counts.get(key)
                    if type(value) is int and 0 <= value <= 2000:
                        safe_counts[key] = value
                verification_evidence = {
                    "passed": receipt.get("passed") is True,
                    "evidence_refs": [
                        ref[:256] for ref in _json_list(receipt.get("evidence_refs"))
                        if isinstance(ref, str) and ref.startswith((
                            "verifier:", "sha256:", "workspace:sha256:", "workspace-baseline:sha256:", "workspace-delta:sha256:"
                        ))
                    ][:256] if isinstance(receipt.get("evidence_refs"), list) else [],
                    "checks": [
                        {"name": check["name"][:128], "passed": check.get("passed") is True}
                        for check in _json_list(receipt.get("checks"))[:64]
                        if isinstance(check, dict) and isinstance(check.get("name"), str)
                    ],
                    "independent_test_runs": safe_runs,
                    "workspace_manifest_before": {
                        "complete": before.get("complete") is True,
                        "file_count": before.get("file_count"),
                    },
                    "workspace_manifest": {
                        "complete": manifest.get("complete") is True,
                        "file_count": manifest.get("file_count"),
                        "returned_file_count": len(safe_manifest),
                        "files": safe_manifest,
                    },
                    "workspace_delta": {
                        "complete": delta.get("complete") is True,
                        "created": safe_manifest_files({"files": delta.get("created", [])}),
                        "modified": [
                            {key: item[key] for key in ("path", "after_size_bytes", "after_sha256") if key in item}
                        for item in _json_list(delta.get("modified"))[:32]
                            if isinstance(item, dict)
                            and isinstance(item.get("path"), str)
                            and len(item["path"]) <= 256
                            and not item["path"].startswith("/")
                            and "\\" not in item["path"]
                            and not any(part in {"", ".", ".."} for part in item["path"].split("/"))
                            and isinstance(item.get("after_sha256"), str)
                            and re.fullmatch(r"[0-9a-f]{64}", item["after_sha256"]) is not None
                            and type(item.get("after_size_bytes")) is int
                            and item["after_size_bytes"] >= 0
                        ] if isinstance(delta.get("modified"), list) else [],
                        "counts": safe_counts,
                    },
                }
            execution_health = "not_running"
            if status == TaskStatus.RUNNING.value:
                current_attempt = next(
                    (
                        attempt for attempt in reversed(task_attempts)
                        if attempt["fencing_token"] == task.fencing_token
                        and attempt["status"] in {"leased", "running", "submitted"}
                    ),
                    None,
                )
                if current_attempt is None:
                    execution_health = "missing_current_attempt"
                elif current_attempt["status"] == "submitted":
                    execution_health = "awaiting_verification"
                elif current_attempt["lease_expires_at"] and current_attempt["lease_expires_at"] <= now:
                    execution_health = "expired_worker_lease"
                elif current_attempt["status"] == "leased":
                    execution_health = "claimed_not_started"
                else:
                    execution_health = "active"

            task_reports.append({
                "task_id": task.task_id,
                "title": task.title,
                "objective": task.objective,
                "acceptance_criteria": task.acceptance_criteria,
                "execution_mode": task.execution_mode.value,
                "status": task.status,
                "retry_count": task.retry_count,
                "max_retries": task.max_retries,
                "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
                "assigned_employee_id": employee.employee_id if employee else None,
                "assigned_employee_name": employee.name if employee else None,
                "deadline": task.deadline,
                "scheduled_at": task.scheduled_at,
                "overdue": overdue,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "verified": verified,
                "execution_health": execution_health,
                "verification_notes": str(receipt.get("notes", ""))[:1000] if receipt else None,
                "evidence_refs": verification_evidence.get("evidence_refs", []) if verified else [],
                "verification_evidence": verification_evidence,
                "attempts": task_attempts,
            })

        return {
            "project_id": project.id,
            "project_name": project.name,
            "objective": project.objective,
            "project_status": project.status,
            "generated_at": now,
            "task_counts": counts,
            "tasks": task_reports,
        }

    async def recruit_workforce_for_plan(self, plan: dict[str, Any]) -> list[Any]:
        """Autonomously recruits the workforce required for an execution plan (Requirements 7 & 12)."""
        if not self.employee_repo:
            return []
        from neural.employees.factory import EmployeeFactory
        factory = EmployeeFactory(self.employee_repo)
        return await factory.recruit_workforce_for_plan(plan)

    async def get_status(self) -> dict[str, Any]:
        settings = self.llm_gateway.settings
        provider_configured = any(
            getattr(settings, name, None)
            for name in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
        )
        worker_credentials = dict(settings.WORKER_API_TOKENS)
        if settings.WORKER_API_TOKEN and len(settings.WORKER_API_TOKEN) >= 32:
            worker_credentials.setdefault("worker-service", settings.WORKER_API_TOKEN)
        worker_token_values = list(worker_credentials.values())
        worker_auth_configured = bool(worker_credentials) and all(
            worker_id.strip() and len(worker_id) <= 128 and token and len(token) >= 32
            for worker_id, token in worker_credentials.items()
        ) and len(set(worker_token_values)) == len(worker_token_values) and not any(
            token == settings.OWNER_API_TOKEN or token == settings.VERIFIER_API_TOKEN
            for token in worker_token_values
        )
        verifier_auth_configured = bool(
            settings.VERIFIER_API_TOKEN
            and len(settings.VERIFIER_API_TOKEN) >= 32
            and settings.VERIFIER_API_TOKEN != settings.OWNER_API_TOKEN
            and settings.VERIFIER_API_TOKEN not in worker_token_values
        )
        heartbeat_rows = None
        if self.service_heartbeat_repo:
            try:
                heartbeat_rows = await self.service_heartbeat_repo.list_all()
            except Exception:
                heartbeat_rows = None

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        service_runtimes: list[dict[str, Any]] = []
        if heartbeat_rows is not None:
            for heartbeat in heartbeat_rows:
                age_seconds = max(0.0, (now - heartbeat.last_seen_at).total_seconds())
                service_runtimes.append({
                    "service_type": heartbeat.service_type,
                    "service_id": heartbeat.service_id,
                    "instance_id": heartbeat.instance_id,
                    "status": "healthy" if age_seconds <= 60 else "stale",
                    "last_seen_at": heartbeat.last_seen_at,
                    "age_seconds": round(age_seconds, 2),
                })

        def runtime_state(service_type: str, expected_ids: set[str]) -> str:
            matches = [item for item in service_runtimes if item["service_type"] == service_type]
            if heartbeat_rows is None and self.service_heartbeat_repo:
                return "unavailable"
            if not matches:
                return "not_reported"
            seen_ids = {item["service_id"] for item in matches}
            all_fresh = all(item["status"] == "healthy" for item in matches)
            if not all_fresh:
                return "stale" if not any(item["status"] == "healthy" for item in matches) else "degraded"
            if not expected_ids.issubset(seen_ids):
                return "degraded"
            return "healthy"

        worker_runtime = runtime_state("worker", set(worker_credentials))
        verifier_runtime = runtime_state("verifier", {"verification-service"} if verifier_auth_configured else set())
        active_missions = None
        active_tasks = None
        if self.project_repo:
            try:
                projects = await self.project_repo.list_all()
                active_missions = sum(
                    1 for project in projects
                    if str(getattr(project.status, "value", project.status)).lower()
                    not in {"completed", "failed", "cancelled"}
                )
            except Exception:
                active_missions = None
        if self.task_repo:
            try:
                tasks = await self.task_repo.list_all()
                active_tasks = sum(
                    1 for task in tasks
                    if str(getattr(task.status, "value", task.status)).lower() == "running"
                )
            except Exception:
                active_tasks = None

        # Credentials only prove a worker could authenticate; they do not prove
        # that a live worker is consuming tasks. Keep that distinction visible.
        return {
            "ceo_status": "ready" if provider_configured else "rules_only",
            "active_mode": "planning_and_delegation" if provider_configured else "rules_based_planning",
            "active_missions": active_missions,
            "active_tasks": active_tasks,
            "llm_provider_configured": provider_configured,
            "worker_auth_configured": worker_auth_configured,
            "verifier_auth_configured": verifier_auth_configured,
            "worker_runtime": worker_runtime,
            "verifier_runtime": verifier_runtime,
            "service_runtimes": service_runtimes if heartbeat_rows is not None else None,
            "system_health": (
                "healthy"
                if (
                    worker_auth_configured
                    and verifier_auth_configured
                    and worker_runtime == "healthy"
                    and verifier_runtime == "healthy"
                )
                else "degraded"
            ),
        }
