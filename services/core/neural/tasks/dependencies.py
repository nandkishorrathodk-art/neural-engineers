from typing import Any, Optional

import networkx as nx

from neural.exceptions import TaskDependencyError
from neural.logger import get_logger

logger = get_logger("ancos.tasks.dependencies")


class TaskGraph:
    """
    Graph-based Task Dependency Engine using NetworkX.
    Nodes represent tasks, and directed edges (u -> v) mean task u must complete
    before task v can start.
    """

    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()

    def add_task(self, task_id: str, **attributes: Any) -> None:
        """Add a task node to the graph with optional attributes."""
        self.graph.add_node(task_id, **attributes)

    def add_dependency(
        self,
        task_id: str,
        depends_on_id: str,
        dependency_type: str = "blocks"
    ) -> None:
        """
        Add a dependency indicating that `task_id` depends on `depends_on_id`.
        Edge is oriented: depends_on_id -> task_id.
        Raises TaskDependencyError if this would introduce a cycle.
        """
        if task_id == depends_on_id:
            raise TaskDependencyError(f"Self-dependency detected: task {task_id} cannot depend on itself.")

        if not self.graph.has_node(task_id):
            self.add_task(task_id)
        if not self.graph.has_node(depends_on_id):
            self.add_task(depends_on_id)

        # Speculatively add edge to check for cycle
        self.graph.add_edge(depends_on_id, task_id, dependency_type=dependency_type)
        if not nx.is_directed_acyclic_graph(self.graph):
            cycles = list(nx.simple_cycles(self.graph))
            self.graph.remove_edge(depends_on_id, task_id)
            raise TaskDependencyError(
                f"Dependency from '{depends_on_id}' to '{task_id}' introduces a cycle: {cycles or [[depends_on_id, task_id]]}"
            )

    def remove_dependency(self, task_id: str, depends_on_id: str) -> None:
        """Remove a dependency edge."""
        if self.graph.has_edge(depends_on_id, task_id):
            self.graph.remove_edge(depends_on_id, task_id)

    def has_cycles(self) -> bool:
        """Check if graph has any cycles."""
        return not nx.is_directed_acyclic_graph(self.graph)

    def detect_cycles(self) -> list[list[str]]:
        """Return list of cycles if any exist."""
        return list(nx.simple_cycles(self.graph))

    def validate_dag(self) -> None:
        """Validate that the graph is a valid DAG. Raises TaskDependencyError on cycles."""
        if self.has_cycles():
            cycles = self.detect_cycles()
            raise TaskDependencyError(f"Task dependency graph contains cycles: {cycles}")

    def topological_sort(self) -> list[str]:
        """
        Return tasks ordered topologically such that prerequisites appear before dependents.
        Raises TaskDependencyError if cycles exist.
        """
        self.validate_dag()
        return list(nx.topological_sort(self.graph))

    def get_dependencies(self, task_id: str) -> list[str]:
        """Return list of direct prerequisite task IDs that task_id depends on."""
        if not self.graph.has_node(task_id):
            return []
        return list(self.graph.predecessors(task_id))

    def get_dependents(self, task_id: str) -> list[str]:
        """Return list of direct successor task IDs that depend on task_id."""
        if not self.graph.has_node(task_id):
            return []
        return list(self.graph.successors(task_id))

    def get_all_tasks(self) -> list[str]:
        """Return all task IDs in graph."""
        return list(self.graph.nodes)

    def get_task_data(self, task_id: str) -> dict[str, Any]:
        """Return attribute dictionary for a task node."""
        if not self.graph.has_node(task_id):
            return {}
        return dict(self.graph.nodes[task_id])

    def get_ready_tasks(
        self,
        completed_task_ids: set[str],
        active_task_ids: Optional[set[str]] = None
    ) -> list[str]:
        """
        Return task IDs that are ready to run:
        - All their dependencies are in `completed_task_ids`
        - The task itself is not in `completed_task_ids`
        - The task itself is not in `active_task_ids` (if provided)
        """
        active = active_task_ids or set()
        ready: list[str] = []

        for task_id in self.graph.nodes:
            if task_id in completed_task_ids or task_id in active:
                continue
            prereqs = set(self.graph.predecessors(task_id))
            if prereqs.issubset(completed_task_ids):
                ready.append(task_id)

        return ready

    def critical_path(self) -> list[str]:
        """Compute the critical (longest) path through the DAG."""
        self.validate_dag()
        if len(self.graph.nodes) == 0:
            return []
        return list(nx.dag_longest_path(self.graph))

    @classmethod
    def from_execution_plan(cls, plan: dict[str, Any]) -> "TaskGraph":
        """
        Build and validate a TaskGraph from a hierarchical execution plan dict
        (as returned by TaskPlanner.decompose_objective).
        Handles both explicit task dependencies and phase-level dependencies.
        """
        tg = cls()
        phases = plan.get("phases", [])

        # Step 1: Add all tasks and map phase_id -> list of task_ids
        phase_to_task_ids: dict[str, list[str]] = {}
        for phase in phases:
            phase_id = phase.get("phase_id", "")
            phase_to_task_ids[phase_id] = []
            for task in phase.get("tasks", []):
                tid = task.get("task_id")
                if tid:
                    tg.add_task(
                        tid,
                        title=task.get("title", ""),
                        role_required=task.get("role_required", ""),
                        skills_required=task.get("skills_required", []),
                        phase_id=phase_id
                    )
                    phase_to_task_ids[phase_id].append(tid)

        # Step 2: Wire dependencies
        for phase in phases:
            for task in phase.get("tasks", []):
                tid = task.get("task_id")
                deps = task.get("dependencies", [])
                for dep in deps:
                    if dep in phase_to_task_ids:
                        # Phase-level dependency: depends on all tasks in that phase
                        for dep_tid in phase_to_task_ids[dep]:
                            if dep_tid != tid:
                                tg.add_dependency(tid, dep_tid)
                    else:
                        # Direct task ID dependency
                        tg.add_dependency(tid, dep)

        tg.validate_dag()
        return tg

