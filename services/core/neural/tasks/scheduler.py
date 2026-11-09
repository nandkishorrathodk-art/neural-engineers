import asyncio
import inspect
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from neural.boss.delegator import TaskDelegator
from neural.constants import EmployeeStatus, TaskPriority, TaskStatus
from neural.db.models import Task
from neural.db.repositories import EmployeeRepository, TaskRepository
from neural.exceptions import TaskDependencyError
from neural.logger import get_logger
from neural.tasks.dependencies import TaskGraph

logger = get_logger("ancos.tasks.scheduler")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TaskScheduler:
    """
    DAG Task Scheduler & Parallel Dispatcher.
    Resolves task graph dependencies, manages concurrency bounds,
    and coordinates parallel employee execution with zero race conditions.
    """

    def __init__(
        self,
        task_repo: Optional[TaskRepository] = None,
        employee_repo: Optional[EmployeeRepository] = None,
        max_concurrency: int = 5,
        message_bus: Optional[Any] = None,
        delegator: Optional[TaskDelegator] = None,
        verifier: Optional[Any] = None,
        recovery_manager: Optional[Any] = None,
        enforce_verification: bool = False,
        max_backoff_delay: Optional[float] = None
    ):
        self.task_repo = task_repo
        self.employee_repo = employee_repo
        self.max_concurrency = max_concurrency
        self.message_bus = message_bus
        self.delegator = delegator or TaskDelegator()
        self.verifier = verifier
        self.recovery_manager = recovery_manager
        self.enforce_verification = enforce_verification
        self.max_backoff_delay = max_backoff_delay
        self._lock = asyncio.Lock()
        self._active_task_ids: set[str] = set()
        self._active_worker_tasks: set[asyncio.Task[Any]] = set()
        try:
            from neural.security.kill_switch import get_kill_switch
            get_kill_switch().register_scheduler(self)
        except Exception:
            pass

    async def cancel_all_active_workers(self) -> int:
        """Emergency stop: cancels all actively executing task worker coroutines."""
        cancelled = 0
        for task in list(self._active_worker_tasks):
            if not task.done():
                task.cancel()
                cancelled += 1
        self._active_worker_tasks.clear()
        self._active_task_ids.clear()
        logger.warning("Cancelled active workers due to emergency halt", cancelled_count=cancelled)
        return cancelled


    async def _free_employee(self, task_id: str) -> None:
        """Resets the assigned employee to IDLE status when their task terminates."""
        if not self.employee_repo or not self.task_repo:
            return
        try:
            db_task = await self.task_repo.get_by_task_id(task_id)
            if db_task and db_task.assigned_to:
                await self.employee_repo.update_status(db_task.assigned_to, EmployeeStatus.IDLE)
        except Exception as e:
            logger.warning("Failed to reset employee status to IDLE", task_id=task_id, error=str(e))

    async def build_graph_for_project(self, project_id: str) -> TaskGraph:
        """
        Builds and validates a TaskGraph from tasks and dependencies stored in the database.
        """
        if not self.task_repo:
            raise ValueError("TaskRepository is required to build graph for project")

        tasks = await self.task_repo.list_by_project(project_id)
        dependencies = await self.task_repo.list_dependencies_for_project(project_id)

        graph = TaskGraph()
        for task in tasks:
            graph.add_task(
                task.task_id,
                db_id=task.id,
                title=task.title,
                status=task.status,
                priority=task.priority,
                required_capabilities=task.required_capabilities or []
            )

        for tid, dep_tid in dependencies:
            try:
                graph.add_dependency(tid, dep_tid)
            except TaskDependencyError as e:
                logger.error("Dependency error while loading graph", error=str(e))
                raise

        graph.validate_dag()
        return graph

    async def get_ready_tasks(self, project_id: str) -> list[Task]:
        """
        Identifies all tasks in a project whose prerequisites have completed
        and are currently eligible for execution.
        """
        if not self.task_repo:
            return []

        tasks = await self.task_repo.list_by_project(project_id)
        task_map = {t.task_id: t for t in tasks}

        completed_ids = {t.task_id for t in tasks if t.status == TaskStatus.COMPLETED}
        active_ids = {
            t.task_id for t in tasks
            if t.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED, TaskStatus.RETRYING)
        }
        active_ids.update(self._active_task_ids)

        graph = await self.build_graph_for_project(project_id)
        ready_ids = graph.get_ready_tasks(
            completed_task_ids=completed_ids,
            active_task_ids=active_ids
        )

        ready_tasks = [
            task_map[tid] for tid in ready_ids
            if (
                tid in task_map
                and task_map[tid].status == TaskStatus.PENDING
                and (task_map[tid].scheduled_at is None or task_map[tid].scheduled_at <= utc_now())
            )
        ]
        return ready_tasks

    async def dispatch_ready_tasks(
        self,
        project_id: str,
        max_dispatch: Optional[int] = None
    ) -> list[Task]:
        """
        Dispatches eligible tasks to available employees up to concurrency limits.
        Protected by asyncio.Lock to prevent race conditions during parallel worker allocation.
        """
        async with self._lock:
            from neural.security.kill_switch import get_kill_switch
            if get_kill_switch().is_halted:
                logger.warning("Emergency stop active; cannot dispatch tasks", project_id=project_id)
                return []

            if not self.task_repo:
                return []

            all_tasks = await self.task_repo.list_by_project(project_id)
            current_running = [
                t for t in all_tasks
                if t.status in (TaskStatus.RUNNING, TaskStatus.ASSIGNED, TaskStatus.RETRYING) or t.task_id in self._active_task_ids
            ]
            available_slots = max(0, self.max_concurrency - len(current_running))
            if max_dispatch is not None:
                available_slots = min(available_slots, max_dispatch)

            if available_slots <= 0:
                logger.info("Concurrency limit reached, deferring task dispatch", active=len(current_running), max=self.max_concurrency)
                return []

            ready_tasks = await self.get_ready_tasks(project_id)
            if not ready_tasks:
                return []

            # Prioritize tasks by priority
            priority_order = {
                TaskPriority.CRITICAL: 0,
                TaskPriority.HIGH: 1,
                TaskPriority.MEDIUM: 2,
                TaskPriority.LOW: 3
            }
            ready_tasks.sort(key=lambda t: priority_order.get(t.priority, 2))

            employees = await self.employee_repo.list_all() if self.employee_repo else []
            available_employees = [e for e in employees if e.status == EmployeeStatus.IDLE]

            dispatched: list[Task] = []
            for task in ready_tasks[:available_slots]:
                # Find best matching employee using TaskDelegator (strictly prioritizes verified skills)
                assignee = self.delegator.select_best_assignee(task, available_employees)

                if assignee:
                    available_employees.remove(assignee)
                    await self.task_repo.assign_task(task.task_id, assignee.id)
                    await self.task_repo.update_status(task.task_id, TaskStatus.RUNNING)
                    await self.employee_repo.update_status(assignee.employee_id, EmployeeStatus.WORKING)
                    task.assigned_to = assignee.id
                    task.status = TaskStatus.RUNNING
                else:
                    # No employee available, mark assigned to keep slot ready or leave pending
                    await self.task_repo.update_status(task.task_id, TaskStatus.RUNNING)
                    task.status = TaskStatus.RUNNING

                self._active_task_ids.add(task.task_id)
                dispatched.append(task)

                # Notify message bus
                if self.message_bus:
                    try:
                        await self.message_bus.publish_task_event(
                            task_id=task.task_id,
                            event_type="task_dispatched",
                            status=TaskStatus.RUNNING.value,
                            details={
                                "title": task.title,
                                "assignee_id": assignee.employee_id if assignee else None
                            }
                        )
                        if assignee:
                            await self.message_bus.send_to_employee(
                                employee_id=assignee.employee_id,
                                message={
                                    "message_type": "task_assignment",
                                    "task_id": task.task_id,
                                    "title": task.title,
                                    "objective": task.objective
                                }
                            )
                    except Exception as e:
                        logger.warning("Failed to emit task dispatch bus event", error=str(e))

            logger.info(
                "Dispatched ready tasks",
                dispatched_count=len(dispatched),
                project_id=project_id
            )
            return dispatched

    async def execute_dag(
        self,
        project_id: str,
        worker_func: Optional[Callable[[Task], Any]] = None,
        poll_interval: float = 0.05
    ) -> dict[str, Any]:
        """
        Executes a project DAG to completion with parallel worker dispatch.
        Monitors dependencies, tracks worker outcomes, resolves deadlock,
        and cascades failure blocks to dependent tasks.
        """
        graph = await self.build_graph_for_project(project_id)
        all_task_ids = set(graph.get_all_tasks())

        if not all_task_ids:
            return {
                "total_tasks": 0,
                "completed": [],
                "failed": [],
                "blocked": [],
                "success": True,
            }
        if worker_func is None:
            raise RuntimeError(
                "DAG execution requires an explicitly configured task worker; "
                "no synthetic success worker is available."
            )

        completed_tasks: set[str] = set()
        failed_tasks: set[str] = set()
        blocked_tasks: set[str] = set()

        if self.task_repo:
            existing_tasks = await self.task_repo.list_by_project(project_id)
            for t in existing_tasks:
                if t.status == TaskStatus.COMPLETED:
                    completed_tasks.add(t.task_id)
                elif t.status == TaskStatus.FAILED:
                    failed_tasks.add(t.task_id)
                elif t.status == TaskStatus.BLOCKED:
                    blocked_tasks.add(t.task_id)

        running_workers: dict[str, asyncio.Task] = {}
        active_worker_func = worker_func

        async def _execute_worker_task(t: Task) -> Any:
            if inspect.iscoroutinefunction(active_worker_func):
                return await active_worker_func(t)
            res = active_worker_func(t)
            if asyncio.iscoroutine(res):
                return await res
            return res

        try:
            while True:
                # Emergency Stop check
                from neural.security.kill_switch import get_kill_switch
                if get_kill_switch().is_halted:
                    logger.warning("Emergency stop active; aborting DAG execution loop", project_id=project_id)
                    for _, fut in running_workers.items():
                        fut.cancel()
                    break

                # 1. Check for finished workers
                done_ids = []
                for tid, fut in list(running_workers.items()):
                    if fut.done():
                        done_ids.append(tid)
                        self._active_task_ids.discard(tid)
                        self._active_worker_tasks.discard(fut)

                        try:
                            res = fut.result()
                            is_success = (
                                isinstance(res, dict) and res.get("status") == "success"
                            ) or res is None or res is True

                            if is_success:
                                if self.verifier is None:
                                    raise RuntimeError(
                                        "Task execution returned success, but no independent "
                                        "verifier is configured."
                                    )
                                # If verifier is configured, execute empirical verification before completion
                                if self.verifier:
                                    db_task = await self.task_repo.get_by_task_id(tid) if self.task_repo else None
                                    emp_id = db_task.assigned_to if db_task else ""
                                    artifacts = res.get("artifacts", []) if isinstance(res, dict) else []
                                    exit_code = res.get("test_exit_code", 0) if isinstance(res, dict) else 0
                                    tested_skills = db_task.required_capabilities if db_task and db_task.required_capabilities else []
                                    v_res = await self.verifier.verify_task_result(
                                        task_id=tid,
                                        employee_id=emp_id or "UNKNOWN",
                                        artifacts=artifacts,
                                        test_exit_code=exit_code,
                                        tested_skills=tested_skills,
                                        acceptance_criteria=db_task.acceptance_criteria if db_task else None
                                    )
                                    if v_res["passed"]:
                                        completed_tasks.add(tid)
                                        if self.task_repo:
                                            await self.task_repo.complete_with_receipt(tid, v_res["receipt"])
                                        await self._free_employee(tid)
                                        if self.message_bus:
                                            await self.message_bus.publish_task_event(
                                                task_id=tid,
                                                event_type="task_completed",
                                                status=TaskStatus.COMPLETED.value,
                                                details={"result": res, "verification": v_res}
                                            )
                                    else:
                                        # Verification failed: consult recovery manager
                                        is_retried = False
                                        if self.recovery_manager and db_task:
                                            rec_action = self.recovery_manager.handle_failure(db_task, v_res["notes"], exit_code=exit_code)
                                            if rec_action.action_type == "retry":
                                                is_retried = True
                                                if self.task_repo:
                                                    await self.task_repo.update_status(tid, TaskStatus.RETRYING)
                                                if self.message_bus:
                                                    try:
                                                        await self.message_bus.publish_task_event(
                                                            task_id=tid,
                                                            event_type="task_retrying",
                                                            status=TaskStatus.RETRYING.value,
                                                            details={"retry_count": rec_action.retry_count, "backoff": rec_action.backoff_delay_seconds}
                                                        )
                                                    except Exception:
                                                        pass
                                                if rec_action.backoff_delay_seconds > 0:
                                                    delay = rec_action.backoff_delay_seconds
                                                    if self.max_backoff_delay is not None:
                                                        delay = min(delay, self.max_backoff_delay)
                                                    await asyncio.sleep(delay)
                                                if self.task_repo:
                                                    await self.task_repo.update_status(tid, TaskStatus.PENDING)
                                        if not is_retried:
                                            failed_tasks.add(tid)
                                            if self.task_repo:
                                                await self.task_repo.update_status(tid, TaskStatus.FAILED)
                                            await self._free_employee(tid)
                                            await self._block_dependents(graph, tid, blocked_tasks)
                                else:
                                    completed_tasks.add(tid)
                                    if self.task_repo:
                                        await self.task_repo.update_status(tid, TaskStatus.COMPLETED, enforce_verification=self.enforce_verification)
                                    await self._free_employee(tid)
                                    if self.message_bus:
                                        await self.message_bus.publish_task_event(
                                            task_id=tid,
                                            event_type="task_completed",
                                            status=TaskStatus.COMPLETED.value,
                                            details={"result": res}
                                        )
                            else:
                                # Task worker reported failure
                                is_retried = False
                                db_task = await self.task_repo.get_by_task_id(tid) if self.task_repo else None
                                if self.recovery_manager and db_task:
                                    err_msg = res.get("error", "Task reported failure") if isinstance(res, dict) else "Task reported failure"
                                    rec_action = self.recovery_manager.handle_failure(db_task, err_msg)
                                    if rec_action.action_type == "retry":
                                        is_retried = True
                                        if self.task_repo:
                                            await self.task_repo.update_status(tid, TaskStatus.RETRYING)
                                        if self.message_bus:
                                            try:
                                                await self.message_bus.publish_task_event(
                                                    task_id=tid,
                                                    event_type="task_retrying",
                                                    status=TaskStatus.RETRYING.value,
                                                    details={"retry_count": rec_action.retry_count, "backoff": rec_action.backoff_delay_seconds}
                                                )
                                            except Exception:
                                                pass
                                        if rec_action.backoff_delay_seconds > 0:
                                            delay = rec_action.backoff_delay_seconds
                                            if self.max_backoff_delay is not None:
                                                delay = min(delay, self.max_backoff_delay)
                                            await asyncio.sleep(delay)
                                        if self.task_repo:
                                            await self.task_repo.update_status(tid, TaskStatus.PENDING)

                                if not is_retried:
                                    failed_tasks.add(tid)
                                    if self.task_repo:
                                        await self.task_repo.update_status(tid, TaskStatus.FAILED)
                                    await self._free_employee(tid)
                                    await self._block_dependents(graph, tid, blocked_tasks)
                        except Exception as err:
                            logger.error("Task worker execution failed", task_id=tid, error=str(err))
                            is_retried = False
                            db_task = await self.task_repo.get_by_task_id(tid) if self.task_repo else None
                            if self.recovery_manager and db_task:
                                rec_action = self.recovery_manager.handle_failure(db_task, err)
                                if rec_action.action_type == "retry":
                                    is_retried = True
                                    if self.task_repo:
                                        await self.task_repo.update_status(tid, TaskStatus.RETRYING)
                                    if self.message_bus:
                                        try:
                                            await self.message_bus.publish_task_event(
                                                task_id=tid,
                                                event_type="task_retrying",
                                                status=TaskStatus.RETRYING.value,
                                                details={"retry_count": rec_action.retry_count, "backoff": rec_action.backoff_delay_seconds}
                                            )
                                        except Exception:
                                            pass
                                    if rec_action.backoff_delay_seconds > 0:
                                        delay = rec_action.backoff_delay_seconds
                                        if self.max_backoff_delay is not None:
                                            delay = min(delay, self.max_backoff_delay)
                                        await asyncio.sleep(delay)
                                    if self.task_repo:
                                        await self.task_repo.update_status(tid, TaskStatus.PENDING)

                            if not is_retried:
                                failed_tasks.add(tid)
                                if self.task_repo:
                                    await self.task_repo.update_status(tid, TaskStatus.FAILED)
                                await self._free_employee(tid)
                                await self._block_dependents(graph, tid, blocked_tasks)

                for tid in done_ids:
                    del running_workers[tid]

                # 2. Check termination condition
                finished_total = len(completed_tasks) + len(failed_tasks) + len(blocked_tasks)
                if finished_total >= len(all_task_ids):
                    break

                # 3. Dispatch newly ready tasks
                available_slots = self.max_concurrency - len(running_workers)
                if available_slots > 0:
                    dispatched = await self.dispatch_ready_tasks(
                        project_id=project_id,
                        max_dispatch=available_slots
                    )
                    for t in dispatched:
                        wt = asyncio.create_task(_execute_worker_task(t))
                        running_workers[t.task_id] = wt
                        self._active_worker_tasks.add(wt)


                # 4. Deadlock detection
                if not running_workers:
                    ready = await self.get_ready_tasks(project_id)
                    if not ready:
                        # Pending tasks remain but none are runnable -> Deadlock!
                        remaining_pending = all_task_ids - (completed_tasks | failed_tasks | blocked_tasks)
                        logger.warning("Deadlock detected in DAG execution", pending=list(remaining_pending))
                        for tid in remaining_pending:
                            blocked_tasks.add(tid)
                            await self.task_repo.update_status(tid, TaskStatus.BLOCKED)
                        break

                await asyncio.sleep(poll_interval)
        except (asyncio.CancelledError, GeneratorExit):
            logger.warning("DAG execution was cancelled mid-flight. Cleaning up workers.", project_id=project_id)
            for tid, fut in running_workers.items():
                fut.cancel()
                self._active_task_ids.discard(tid)
            raise

        return {
            "project_id": project_id,
            "total_tasks": len(all_task_ids),
            "completed": list(completed_tasks),
            "failed": list(failed_tasks),
            "blocked": list(blocked_tasks),
            "success": len(completed_tasks) == len(all_task_ids)
        }

    async def _block_dependents(
        self,
        graph: TaskGraph,
        failed_task_id: str,
        blocked_set: set[str]
    ) -> None:
        """
        Recursively marks all downstream dependent tasks as BLOCKED
        to prevent deadlock when an upstream prerequisite fails.
        """
        dependents = graph.get_dependents(failed_task_id)
        for dep_id in dependents:
            if dep_id not in blocked_set:
                blocked_set.add(dep_id)
                self._active_task_ids.discard(dep_id)
                if self.task_repo:
                    await self.task_repo.update_status(dep_id, TaskStatus.BLOCKED)
                if self.message_bus:
                    try:
                        await self.message_bus.publish_task_event(
                            task_id=dep_id,
                            event_type="task_blocked",
                            status=TaskStatus.BLOCKED.value,
                            details={"blocked_by": failed_task_id}
                        )
                    except Exception:
                        pass
                await self._block_dependents(graph, dep_id, blocked_set)
