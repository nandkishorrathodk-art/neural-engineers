"""Regression tests for NEURO-12B Scope A.

A hybrid task's terminal phase is authored by the model. When the model emits a
malformed test script, the command exits non-zero and the task fails for a reason
that has nothing to do with the work it represents. These tests lock in that such
a script is rejected before execution, the concrete problem is fed back into the
replan, and the rejected output is preserved as diagnostic evidence.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session, get_llm_gateway
from neural.api.main import create_app
from neural.api.routes import worker as worker_routes
from neural.config import get_settings
from neural.constants import ContainerStatus, EmployeeStatus, ExecutionMode, TaskStatus
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.verification.generated_script import (
    validate_generated_commands,
    validate_generated_files,
    validate_generated_python,
)
from neural.verification.test_commands import scope_test_commands_to_plan
from neural.workers.terminal import TerminalPlan, WorkspaceFile, _write_commands

WORKER_ID = "worker-generated-script-test"
WORKER_TOKEN = "worker-generated-script-test-" + "w" * 40


def _kinds(problems):
    return sorted(problem.kind for problem in problems)


def test_syntax_error_is_reported():
    problems = validate_generated_python("test_x.py", 'def test_x():\n    s = """abc\n    return 1\n')
    assert _kinds(problems) == ["syntax_error"]


def test_undefined_name_is_reported():
    problems = validate_generated_python(
        "test_x.py",
        "import pytest\n\n\ndef test_a():\n    assert reference_to_nothing is None\n",
    )
    assert _kinds(problems) == ["undefined_name"]
    assert "reference_to_nothing" in problems[0].render()


def test_invalid_pytest_api_is_reported():
    problems = validate_generated_python(
        "test_x.py", "import pytest\n\n\ndef test_a():\n    pytest.SubRequest('x')\n"
    )
    assert _kinds(problems) == ["invalid_pytest_api"]
    assert "pytest.SubRequest" in problems[0].render()


def test_clean_test_script_is_accepted():
    content = (
        "import hashlib\n"
        "import pytest\n"
        "\n"
        "\n"
        "@pytest.fixture\n"
        "def artifact(tmp_path):\n"
        "    return tmp_path\n"
        "\n"
        "\n"
        "def test_handoff_hash(artifact):\n"
        "    digest = hashlib.sha256(b'x').hexdigest()\n"
        "    assert len(digest) == 64\n"
    )
    assert validate_generated_python("test_handoff.py", content) == []


def test_star_import_makes_undefined_names_unprovable():
    # ``from os.path import *`` can bind any name, so an undefined-name verdict is
    # not provable and must not be reported.
    problems = validate_generated_python(
        "test_x.py", "from os.path import *\n\n\ndef test_a():\n    assert exists('/') or not exists('/')\n"
    )
    assert problems == []


def test_validate_generated_files_ignores_non_python_artifacts():
    problems = validate_generated_files(
        [
            ("notes.txt", "def broken(:\n"),
            ("handoff.md", "def broken(:\n"),
            ("test_ok.py", "def test_ok():\n    assert True\n"),
        ]
    )
    assert problems == []


def test_script_persisted_as_a_raw_write_command_is_validated():
    """A model that bypasses the ``files`` field still gets its script checked."""
    content = (
        "import pytest\n"
        "\n"
        "\n"
        "def test_handoff():\n"
        "    assert computed_sha256 is not None\n"
    )
    commands = [*_write_commands("test_handoff.py", content), ["python3", "-m", "pytest", "-q"]]
    problems = validate_generated_commands(commands)
    assert [problem.kind for problem in problems] == ["undefined_name"]
    assert problems[0].path == "test_handoff.py"


def test_clean_script_persisted_as_a_write_command_is_accepted():
    commands = _write_commands("test_ok.py", "def test_ok():\n    assert True\n")
    assert validate_generated_commands(commands) == []


def test_multichunk_write_command_reassembles_before_validation():
    """A script longer than one base64 chunk must be reassembled in argv order."""
    content = "x = 1\n" * 4000  # comfortably more than one 4000-char base64 chunk
    commands = _write_commands("big.py", content)
    assert len(commands) > 1
    # The reassembled script is valid, so no problems are reported even though it
    # spans several commands.
    assert validate_generated_commands(commands) == []


def test_bare_pytest_is_scoped_to_the_persisted_test_module():
    """A bare `pytest -q` must not collect leftover modules from earlier objectives."""
    commands = [
        *_write_commands("test_handoff.py", "def test_ok():\n    assert True\n"),
        ["python3", "-m", "pytest", "-q"],
    ]
    scoped = scope_test_commands_to_plan(commands)
    assert scoped[-1] == ["python3", "-m", "pytest", "-q", "test_handoff.py"]


def test_pytest_with_explicit_path_is_left_alone():
    commands = [["python3", "-m", "pytest", "-q", "test_specific.py"]]
    assert scope_test_commands_to_plan(commands) == commands


def test_no_persisted_test_module_leaves_commands_unchanged():
    commands = [["python3", "-m", "pytest", "-q"]]
    assert scope_test_commands_to_plan(commands) == commands


@pytest.mark.asyncio
async def test_malformed_generated_script_is_rejected_and_replanned(
    async_session, monkeypatch, tmp_path
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    monkeypatch.setattr(
        worker_routes, "_GENERATION_EVIDENCE_DIR", str(tmp_path / "generation_failures")
    )
    get_settings.cache_clear()

    employee = await EmployeeRepository(async_session).create(
        employee_id="ANC-EMP-GEN-SCRIPT",
        name="Generated Script Validation",
        title="Software Engineer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-gen-script",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/gen-script-validation",
    )
    await EmployeeRepository(async_session).assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="Generated script", objective="Reject malformed generated scripts"
    )
    task_repo = TaskRepository(async_session)
    task = await task_repo.create(
        task_id="TASK-GEN-SCRIPT",
        project_id=project.id,
        assigned_to=employee.id,
        title="Create test_handoff.py with a handoff hash check",
        objective="Implement and persist test_handoff.py in the workspace",
        scope="Task workspace",
        acceptance_criteria="Tests pass",
        execution_mode=ExecutionMode.TERMINAL,
        status=TaskStatus.ASSIGNED,
        resource_budget={"max_tokens": 500},
    )
    claim = await task_repo.claim_next_task(WORKER_ID, execution_modes=[ExecutionMode.TERMINAL])
    assert claim is not None
    await task_repo.start_task_attempt(
        task.task_id,
        WORKER_ID,
        claim["fencing_token"],
        {
            "captured_at_unix": 1790000000.0,
            "files": [],
            "file_count": 0,
            "total_size_bytes": 0,
            "complete": True,
            "errors": [],
        },
    )

    class GatewayStub:
        calls = []
        plans = [
            # Malformed: references a name that is never defined and calls an
            # attribute that does not exist in the pytest 6.2.5 runtime.
            TerminalPlan(
                files=[
                    WorkspaceFile(
                        path="test_handoff.py",
                        content=(
                            "import pytest\n"
                            "\n"
                            "\n"
                            "def test_handoff():\n"
                            "    assert computed_sha256 is not None\n"
                            "    pytest.SubRequest('x')\n"
                        ),
                    )
                ],
                commands=[["python3", "-m", "pytest", "-q"]],
            ),
            # Repaired: valid script.
            TerminalPlan(
                files=[
                    WorkspaceFile(
                        path="test_handoff.py",
                        content="def test_handoff():\n    assert True\n",
                    )
                ],
                commands=[["python3", "-m", "pytest", "-q"]],
            ),
        ]
        cost_tracker = SimpleNamespace(
            max_project_budget_usd=150.0,
            max_task_budget_usd=10.0,
            monthly_hard_cap_usd=500.0,
        )

        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            plan = self.plans[len(self.calls) - 1]
            return plan, SimpleNamespace(
                provider="test-provider",
                model="test-model",
                input_tokens=10,
                output_tokens=5,
                cost_usd=0.001,
            )

    gateway = GatewayStub()
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    assert len(gateway.calls) == 2
    # The second attempt must carry the concrete rejection so the model can repair.
    assert "previous plan was rejected before execution" in gateway.calls[1]["prompt"]
    assert "undefined_name" in gateway.calls[1]["prompt"]
    assert "invalid_pytest_api" in gateway.calls[1]["prompt"]

    # The rejected output and the rules that rejected it are preserved verbatim.
    evidence_dir = tmp_path / "generation_failures" / task.task_id
    evidence_files = sorted(evidence_dir.glob("*.json"))
    assert len(evidence_files) == 1
    payload = json.loads(evidence_files[0].read_text(encoding="utf-8"))
    assert payload["planning_attempt"] == 1
    assert any("computed_sha256" in problem for problem in payload["problems"])
    assert payload["files"][0]["path"] == "test_handoff.py"
    assert "computed_sha256" in payload["files"][0]["content"]
