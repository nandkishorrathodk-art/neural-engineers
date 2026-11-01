from typing import TYPE_CHECKING, Any, Callable, Optional

from neural.boss.delegator import TaskDelegator
from neural.boss.planner import TaskPlanner
from neural.boss.recovery import FailureRecoveryManager
from neural.constants import TaskPriority, TaskStatus
from neural.db.models import Project, Task
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository
from neural.llm.gateway import LLMGateway
from neural.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("ancos.boss")


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
        message_bus: Optional[Any] = None
    ):
        self.planner = planner or TaskPlanner()
        self.project_repo = project_repo
        self.task_repo = task_repo
        self.employee_repo = employee_repo
        self.message_bus = message_bus
        self.delegator = delegator or TaskDelegator()
        self.verifier = verifier
        self.recovery_manager = recovery_manager or FailureRecoveryManager()
        self.llm_gateway = llm_gateway or LLMGateway()
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

    async def analyze_and_plan(self, objective: str) -> dict[str, Any]:
        from neural.exceptions import EmergencyStopTriggered
        from neural.security.kill_switch import get_kill_switch

        get_kill_switch().guard_active()
        if self._is_paused:
            raise EmergencyStopTriggered("BossBrain is currently paused due to emergency halt.")

        logger.info("Boss AI analyzing incoming objective", objective=objective)
        plan = await self.planner.decompose_objective(objective)
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
        auto_recruit_workforce: bool = False
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

        plan = await self.analyze_and_plan(objective)
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
                        required_capabilities=task_info.get("skills_required", []),
                        priority=priority,
                        status=TaskStatus.PENDING,
                        resource_budget=task_info.get("resource_budget", {"estimated_hours": 4, "max_tokens": 50000})
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
                if not employees and auto_recruit_workforce:
                    from neural.employees.factory import EmployeeFactory
                    factory = EmployeeFactory(e_repo)
                    employees = await factory.recruit_workforce_for_plan(plan)

                if employees:
                    first_task = created_tasks[0]
                    # Prioritize employee with empirically verified matching skills
                    assignee = self.delegator.select_best_assignee(first_task, employees)
                    if not assignee:
                        assignee = employees[0]
                        first_task_info = phases[0].get("tasks", [{}])[0] if phases else {}
                        req_role = first_task_info.get("role_required", "")
                        for emp in employees:
                            if req_role and req_role.replace("-", " ").lower() in emp.title.lower():
                                assignee = emp
                                break

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

    async def recruit_workforce_for_plan(self, plan: dict[str, Any]) -> list[Any]:
        """Autonomously recruits the workforce required for an execution plan (Requirements 7 & 12)."""
        if not self.employee_repo:
            return []
        from neural.employees.factory import EmployeeFactory
        factory = EmployeeFactory(self.employee_repo)
        return await factory.recruit_workforce_for_plan(plan)

    async def get_status(self) -> dict[str, Any]:
        return {
            "ceo_status": "operational",
            "active_mode": "autonomous_orchestration",
            "active_missions": 1,
            "system_health": "nominal"
        }
