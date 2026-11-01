import pytest
from neural.db.repositories import EmployeeRepository
from neural.employees.factory import EmployeeFactory
from neural.evaluation.verifier import IndependentVerifier
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_hire_and_verify_employee_skills(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    employee = await factory.hire_employee(
        name="Alex Morgan",
        title="Senior Backend Engineer",
        initial_skills=["Python", "FastAPI"]
    )

    assert employee.name == "Alex Morgan"
    assert employee.employee_id.startswith("ANC-EMP-")
    assert len(employee.skills) == 2
    # Skills initially unverified
    assert all(not s.verified for s in employee.skills)

    # Independent QA verification
    verifier = IndependentVerifier(repo)
    verification = await verifier.verify_task_result(
        task_id="TASK-TEST-01",
        employee_id=employee.employee_id,
        artifacts=["/workspace/api/main.py"],
        test_exit_code=0,
        tested_skills=["Python", "FastAPI"]
    )

    assert verification["passed"] is True

    # Re-fetch employee and check verified skills
    updated_emp = await repo.get_by_employee_id(employee.employee_id)
    assert updated_emp is not None
    verified_skills = [s for s in updated_emp.skills if s.verified]
    assert len(verified_skills) == 2
    assert "TASK-TEST-01" in verified_skills[0].evidence_task_ids

