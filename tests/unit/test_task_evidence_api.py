import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.config import get_settings
from neural.constants import EmployeeStatus, TaskStatus
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository

OWNER_TOKEN = "task-evidence-owner-" + "o" * 40


@pytest.mark.asyncio
async def test_task_routes_expose_verification_receipts(async_session, monkeypatch):
    """The dashboard needs the verifier receipt, not just a status string.

    Without it the UI can only show "completed", which is indistinguishable from
    a task that finished without any independent confirmation. The receipt is
    the ground-truth evidence a human reviews.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OWNER_API_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("APPROVER_API_TOKEN", "task-evidence-approver-" + "a" * 40)
    get_settings.cache_clear()

    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-EVIDENCE-1",
        name="Evidence Employee",
        title="Software Engineer",
        system_prompt="Test task evidence exposure",
        status=EmployeeStatus.IDLE,
    )
    project = await ProjectRepository(async_session).create(
        name="Evidence project",
        objective="Expose verification receipts through the API",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-EVIDENCE-1",
        project_id=project.id,
        assigned_to=employee.id,
        title="Verified task",
        objective="Return the receipt",
        scope="API contract",
        acceptance_criteria="The task response carries verification_result",
        status=TaskStatus.COMPLETED,
        verification_result={
            "passed": True,
            "notes": "Independent verifier confirmed the deliverable.",
            "checks": [{"name": "tests_passed", "passed": True}],
        },
    )

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        direct = await client.get(f"/tasks/{task.task_id}", headers=headers)
        by_employee = await client.get(f"/employees/{employee.employee_id}/tasks", headers=headers)

    get_settings.cache_clear()

    assert direct.status_code == 200
    assert direct.json()["verification_result"]["passed"] is True
    assert direct.json()["verification_result"]["checks"][0]["name"] == "tests_passed"

    assert by_employee.status_code == 200
    listed = {item["task_id"]: item for item in by_employee.json()}
    assert listed["TASK-EVIDENCE-1"]["verification_result"]["notes"].startswith("Independent")
