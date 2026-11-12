"""Regression tests for the GUI lifecycle dispatch deadlock.

A GUI task recruited an employee whose declared capabilities were
``["terminal"]`` and then matched it by skill alone. Every dispatcher
re-checked capability, rejected the assignment, and the task never left
PENDING: an infinite re-dispatch loop. These tests pin the two root causes:

1. ``TaskDelegator.select_best_assignee`` must never pick an employee whose
   execution capabilities cannot run the task's mode.
2. ``EmployeeFactory`` must grant a recruited employee the capabilities its
   recruiting task's execution mode requires.
"""

import pytest
from neural.boss.delegator import TaskDelegator
from neural.constants import EmployeeStatus, ExecutionMode, SkillLevel
from neural.db.models import Employee, EmployeeSkill, Task, TaskStatus
from neural.db.repositories import EmployeeRepository
from neural.employees.factory import EmployeeFactory
from sqlalchemy.ext.asyncio import AsyncSession


def _employee(employee_id: str, capabilities: list[str] | None, skills: list[str]) -> Employee:
    employee = Employee(
        employee_id=employee_id,
        name=employee_id,
        title="Test Employee",
        department="Engineering",
        system_prompt="test",
        approved_tools=["terminal"],
        status=EmployeeStatus.IDLE,
        execution_capabilities=capabilities,
        performance_summary={"completed_tasks": 0, "verified_skills": []},
    )
    employee.skills = [
        EmployeeSkill(skill_name=skill, level=SkillLevel.INTERMEDIATE, verified=False)
        for skill in skills
    ]
    return employee


def _gui_task(required_capabilities: list[str] | None = None) -> Task:
    return Task(
        task_id="TASK-GUI-DELEGATE",
        project_id="PROJ-GUI",
        title="Type marker on desktop",
        objective="Type marker via the graphical desktop",
        scope="desktop",
        acceptance_criteria="marker visible",
        execution_mode=ExecutionMode.GUI,
        status=TaskStatus.PENDING,
        required_capabilities=required_capabilities or [],
    )


def test_select_best_assignee_excludes_capability_mismatch():
    """A skill match without the mode's capability must not be selected.

    The terminal-only employee has the exact required skill; the GUI-capable
    employee also carries it. Selecting the terminal-only employee strands the
    task because the worker claim route rejects a task the assignee's mode
    cannot run, so the delegator must return the capable employee.
    """
    terminal_only = _employee("ANC-EMP-TERMONLY", ["terminal"], ["GUI automation"])
    gui_capable = _employee("ANC-EMP-GUICAP", ["terminal", "gui"], ["GUI automation"])

    chosen = TaskDelegator().select_best_assignee(
        _gui_task(["GUI automation"]), [terminal_only, gui_capable]
    )
    assert chosen is not None
    assert chosen.employee_id == "ANC-EMP-GUICAP"


def test_select_best_assignee_excludes_skill_mismatch():
    """A capable employee without the required skill must not be selected.

    The worker claim route refuses a task whose assignee's skills do not
    intersect ``required_capabilities``. Assignment must apply the same gate, or
    it pins the task to an employee that can never claim it.
    """
    unskilled_but_capable = _employee("ANC-EMP-NOSKILL", ["terminal", "gui"], ["Python"])
    skilled = _employee("ANC-EMP-SKILLED", ["terminal", "gui"], ["GUI automation"])

    chosen = TaskDelegator().select_best_assignee(
        _gui_task(["GUI automation"]), [unskilled_but_capable, skilled]
    )
    assert chosen is not None
    assert chosen.employee_id == "ANC-EMP-SKILLED"
    assert (
        TaskDelegator().select_best_assignee(_gui_task(["GUI automation"]), [unskilled_but_capable])
        is None
    )


def test_select_best_assignee_returns_none_when_no_capable_employee():
    """With only terminal-capable employees, a GUI task has no valid assignee."""
    terminal_only = _employee("ANC-EMP-TERMONLY2", ["terminal"], ["GUI automation"])
    assert TaskDelegator().select_best_assignee(_gui_task(["GUI automation"]), [terminal_only]) is None


def test_select_best_assignee_still_honours_legacy_empty_capabilities():
    """Employees created before capabilities existed remain terminal-capable."""
    legacy = _employee("ANC-EMP-LEGACY", None, ["Python"])
    task = _gui_task()
    task.execution_mode = ExecutionMode.TERMINAL
    assert TaskDelegator().select_best_assignee(task, [legacy]) is not None


@pytest.mark.asyncio
async def test_recruited_gui_role_gains_gui_capability(async_session: AsyncSession):
    """Recruitment from a GUI plan must grant the GUI capability it will need."""
    factory = EmployeeFactory(EmployeeRepository(async_session))
    plan = {
        "phases": [
            {
                "phase_id": "PHASE-01",
                "tasks": [
                    {
                        "task_id": "T1",
                        "title": "Desktop automation",
                        "role_required": "desktop-automation-operator",
                        "skills_required": ["GUI automation"],
                        "execution_mode": "gui",
                    }
                ],
            }
        ]
    }

    recruited = await factory.recruit_workforce_for_plan(plan)
    assert len(recruited) == 1
    assert "gui" in (recruited[0].execution_capabilities or [])


@pytest.mark.asyncio
async def test_recruited_terminal_role_stays_terminal_only(async_session: AsyncSession):
    """A terminal-only plan must not grant GUI capability."""
    factory = EmployeeFactory(EmployeeRepository(async_session))
    plan = {
        "phases": [
            {
                "phase_id": "PHASE-01",
                "tasks": [
                    {
                        "task_id": "T1",
                        "title": "Backend work",
                        "role_required": "senior-backend-engineer",
                        "skills_required": ["Python"],
                        "execution_mode": "terminal",
                    }
                ],
            }
        ]
    }

    recruited = await factory.recruit_workforce_for_plan(plan)
    assert len(recruited) == 1
    assert "gui" not in (recruited[0].execution_capabilities or [])
