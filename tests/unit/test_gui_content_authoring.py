"""Regression tests for model-driven GUI content authoring.

The deterministic GUI planner can type a *literal* the objective already contains.
It cannot invent written content, so an objective like "draft a short note about
ANCOS" used to produce a no-op plan (``WAIT_SETTLE``) that never placed anything on
screen. These tests lock in the fix: the reasoning model authors the exact text
and a short OCR-readable marker, the ``/gui/plan`` endpoint returns a deterministic
typing choreography for it, and the plan fails closed (no false success) when
authoring is unavailable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session, get_llm_gateway
from neural.api.main import create_app
from neural.config import get_settings
from neural.constants import (
    ContainerStatus,
    EmployeeStatus,
    ExecutionMode,
    TaskPriority,
    TaskStatus,
)
from neural.db.repositories import (
    ContainerRepository,
    EmployeeRepository,
    ProjectRepository,
    TaskRepository,
)
from neural.gui_intelligence.runtime.authoring import (
    AuthoredDesktopContent,
    author_desktop_content,
    build_authoring_prompt,
    build_authoring_steps,
    is_authoring_objective,
    subject_hint,
)
from neural.gui_intelligence.runtime.planner import app_launch_prefix

WORKER_ID = "gui-authoring-test"
WORKER_TOKEN = "gui-authoring-test-" + "w" * 40


def test_authoring_objective_detection_requires_a_composition_verb():
    assert is_authoring_objective("Draft a short note about ANCOS in the text editor")
    assert is_authoring_objective("Write a one-line welcome message")
    assert is_authoring_objective("Create a doc in Google Docs")
    assert is_authoring_objective("Open the text editor and compose a welcome paragraph")
    assert not is_authoring_objective("Launch the web browser and open docs.google.com")
    assert not is_authoring_objective("Open the text editor")
    assert not is_authoring_objective("Create a folder called reports")


def test_subject_hint_extracts_the_named_subject():
    # A trailing location phrase ("in the editor") is not part of the subject.
    assert subject_hint("Draft a short note about ANCOS in the editor") == "ANCOS"
    assert subject_hint("Write a paragraph describing the ANCOS platform") == "the ANCOS platform"
    assert subject_hint("Open the browser") is None


def test_build_authoring_prompt_carries_the_task_contract():
    prompt = build_authoring_prompt(
        "Write ANCOS documentation note",
        "Draft a brief note about ANCOS in the text editor",
        scope="Employee workspace",
        acceptance_criteria="The note names ANCOS",
    )
    assert "Draft a brief note about ANCOS in the text editor" in prompt
    assert "The content is about:" in prompt
    assert "marker" in prompt


def test_authored_marker_is_normalized_to_a_screenshot_safe_alphabet():
    content = AuthoredDesktopContent(
        marker="  ancos note #1  ", body="ANCOS-NOTE-1 about ANCOS"
    )
    assert content.marker == "ANCOS-NOTE-1"
    assert content.marker in content.body


def test_authored_marker_too_short_is_rejected():
    with pytest.raises(ValueError):
        AuthoredDesktopContent(marker="HI", body="HI there")


def test_authored_body_must_not_be_empty():
    with pytest.raises(ValueError):
        AuthoredDesktopContent(marker="ANCOS-NOTE", body="   ")


def test_build_authoring_steps_types_body_after_launch_prefix():
    content = AuthoredDesktopContent(marker="ANCOS-NOTE", body="ANCOS-NOTE about ANCOS")
    prefix = [{"action_type": "MOUSE_CLICK", "target": "Mousepad", "confidence": 0.95}]
    steps = build_authoring_steps(content, prefix)
    assert steps[0] == prefix[0]
    assert steps[1]["action_type"] == "WAIT_SETTLE"
    assert steps[-1]["action_type"] == "KEY_TYPE"
    assert steps[-1]["text"] == content.body


def test_build_authoring_steps_splits_multiline_body_into_return_keypresses():
    content = AuthoredDesktopContent(marker="ANCOS-NOTE", body="line one\nANCOS-NOTE")
    steps = build_authoring_steps(content)
    action_types = [step["action_type"] for step in steps]
    assert action_types == ["KEY_TYPE", "KEY_HOTKEY", "KEY_TYPE"]
    assert steps[1]["key_code"] == "Return"
    assert steps[2]["text"] == "ANCOS-NOTE"


def test_app_launch_prefix_routes_a_bare_web_destination_through_the_browser():
    blob = "Create a doc in Google Docs"
    steps, label = app_launch_prefix(
        blob.lower(), blob.lower(), blob, require_launch_verb=False, allow_web_only=True
    )
    assert label == "Web Browser"
    assert steps[0]["target"] == "Applications"
    assert any(step.get("text") == "docs.google.com" for step in steps)


def test_app_launch_prefix_routes_a_named_desktop_app_without_a_launch_verb():
    blob = "Open the text editor and compose a welcome paragraph"
    steps, label = app_launch_prefix(
        blob.lower(), blob.lower(), blob, require_launch_verb=False, allow_web_only=True
    )
    assert label == "Mousepad"
    assert [step["target"] for step in steps] == ["Applications", "Accessories", "Mousepad"]


def test_app_launch_prefix_returns_nothing_when_no_target_is_named():
    blob = "Draft a short note about ANCOS"
    steps, label = app_launch_prefix(
        blob.lower(), blob.lower(), blob, require_launch_verb=False, allow_web_only=True
    )
    assert steps == []
    assert label is None


class _AuthoringGateway:
    def __init__(self, content: AuthoredDesktopContent | None) -> None:
        self.content = content
        self.calls: list[dict] = []
        self.cost_tracker = SimpleNamespace(
            max_project_budget_usd=150.0,
            max_task_budget_usd=10.0,
            monthly_hard_cap_usd=500.0,
        )

    async def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.content is None:
            raise RuntimeError("provider unavailable")
        return self.content, SimpleNamespace(
            provider="test-provider",
            model="test-model",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
        )


@pytest.mark.asyncio
async def test_author_desktop_content_returns_model_output():
    gateway = _AuthoringGateway(
        AuthoredDesktopContent(marker="ANCOS-NOTE", body="ANCOS-NOTE about ANCOS")
    )
    content = await author_desktop_content(
        gateway,
        title="Write ANCOS documentation note",
        objective="Draft a brief note about ANCOS in the text editor",
    )
    assert content is not None
    assert content.marker == "ANCOS-NOTE"
    assert gateway.calls[0]["route"] == "reasoning"


@pytest.mark.asyncio
async def test_author_desktop_content_fails_closed_when_provider_errors():
    gateway = _AuthoringGateway(None)
    content = await author_desktop_content(
        gateway,
        title="Write ANCOS documentation note",
        objective="Draft a brief note about ANCOS in the text editor",
    )
    assert content is None


@pytest.mark.asyncio
async def test_author_desktop_content_rejects_marker_absent_from_body():
    gateway = _AuthoringGateway(
        AuthoredDesktopContent(marker="ANCOS-NOTE", body="a paragraph with no marker inside")
    )
    content = await author_desktop_content(
        gateway,
        title="Write ANCOS documentation note",
        objective="Draft a brief note about ANCOS in the text editor",
    )
    assert content is None


async def _make_gui_task(async_session, monkeypatch, *, objective: str, title: str):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_API_TOKENS", f'{{"{WORKER_ID}":"{WORKER_TOKEN}"}}')
    monkeypatch.setenv("WORKER_API_TOKEN", "")
    get_settings.cache_clear()

    employee_repo = EmployeeRepository(async_session)
    employee = await employee_repo.create(
        employee_id="ANC-EMP-AUTHORING",
        name="Authoring Validation",
        title="Technical Writer",
        system_prompt="Test employee",
        status=EmployeeStatus.IDLE,
        execution_capabilities=["terminal", "gui"],
    )
    container = await ContainerRepository(async_session).create(
        docker_id="ancos-desktop-anc-emp-authoring",
        image="ancos/employee-base:latest",
        status=ContainerStatus.RUNNING,
        resource_limits={},
        network_config={},
        workspace_path="/tmp/gui-authoring-validation",
    )
    await employee_repo.assign_container(employee.employee_id, container.id)
    project = await ProjectRepository(async_session).create(
        name="Authoring",
        objective="Model-driven GUI content authoring",
    )
    task = await TaskRepository(async_session).create(
        task_id="TASK-GUI-AUTHORING",
        project_id=project.id,
        assigned_to=employee.id,
        title=title,
        objective=objective,
        scope="Employee workspace",
        acceptance_criteria="The note names ANCOS",
        execution_mode=ExecutionMode.GUI,
        priority=TaskPriority.HIGH,
        status=TaskStatus.ASSIGNED,
    )
    claim = await TaskRepository(async_session).claim_next_task(
        WORKER_ID,
        execution_modes=[ExecutionMode.GUI],
    )
    assert claim is not None
    await TaskRepository(async_session).start_task_attempt(
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
    return task, claim


@pytest.mark.asyncio
async def test_gui_plan_authors_content_when_no_literal_payload_resolves(
    async_session, monkeypatch
):
    task, claim = await _make_gui_task(
        async_session,
        monkeypatch,
        title="Write ANCOS documentation note",
        objective="Draft a brief note about ANCOS in the text editor",
    )
    gateway = _AuthoringGateway(
        AuthoredDesktopContent(marker="ANCOS-NOTE", body="ANCOS-NOTE about ANCOS")
    )
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/gui/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    # The marker — not the whole paragraph — is the on-screen pass condition.
    assert payload["expected_text"] == "ANCOS-NOTE"
    assert payload["steps"][-1]["action_type"] == "KEY_TYPE"
    assert payload["steps"][-1]["text"] == "ANCOS-NOTE about ANCOS"
    # The objective names the text editor, so the plan opens it before typing.
    assert [step["target"] for step in payload["steps"][:3]] == [
        "Applications",
        "Accessories",
        "Mousepad",
    ]
    # The authored content is derived from the task contract, not hardcoded: the
    # model saw the objective and the subject.
    assert "Draft a brief note about ANCOS in the text editor" in gateway.calls[0]["prompt"]
    assert gateway.calls[0]["db_session"] is async_session
    assert gateway.calls[0]["task_id"] == task.task_id


@pytest.mark.asyncio
async def test_gui_plan_authors_into_a_web_destination(async_session, monkeypatch):
    task, claim = await _make_gui_task(
        async_session,
        monkeypatch,
        title="Create a doc in Google Docs",
        objective="Create a doc in Google Docs",
    )
    gateway = _AuthoringGateway(
        AuthoredDesktopContent(marker="ANCOS-DOC", body="ANCOS-DOC welcome to ANCOS")
    )
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/gui/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["expected_text"] == "ANCOS-DOC"
    # A bare web destination is opened in the browser before the content is typed.
    assert payload["steps"][1]["target"] == "Web Browser"
    assert any(step.get("text") == "docs.google.com" for step in payload["steps"])
    assert payload["steps"][-1]["action_type"] == "KEY_TYPE"
    assert payload["steps"][-1]["text"] == "ANCOS-DOC welcome to ANCOS"


@pytest.mark.asyncio
async def test_gui_plan_fails_closed_when_authoring_unavailable(async_session, monkeypatch):
    task, claim = await _make_gui_task(
        async_session,
        monkeypatch,
        title="Write ANCOS documentation note",
        objective="Draft a brief note about ANCOS in the text editor",
    )
    gateway = _AuthoringGateway(None)
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/gui/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    # No authored content: the deterministic no-op plan is returned so the worker
    # fails closed rather than reporting a success that never touched the screen.
    assert payload["expected_text"] is None
    assert all(step["action_type"] == "WAIT_SETTLE" for step in payload["steps"])


@pytest.mark.asyncio
async def test_gui_plan_fails_closed_when_objective_names_no_target(async_session, monkeypatch):
    """Authored content with no application to put it in must not be typed blind."""
    task, claim = await _make_gui_task(
        async_session,
        monkeypatch,
        title="Write a note",
        objective="Draft a short note about ANCOS",
    )
    gateway = _AuthoringGateway(
        AuthoredDesktopContent(marker="ANCOS-NOTE", body="ANCOS-NOTE about ANCOS")
    )
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/gui/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["expected_text"] is None
    assert all(step["action_type"] == "WAIT_SETTLE" for step in payload["steps"])


@pytest.mark.asyncio
async def test_gui_plan_does_not_author_when_a_literal_payload_exists(async_session, monkeypatch):
    task, claim = await _make_gui_task(
        async_session,
        monkeypatch,
        title="Type the ANCOS marker",
        objective='Open the text editor and type "ANCOS_LIVE_OK"',
    )
    gateway = _AuthoringGateway(
        AuthoredDesktopContent(marker="OTHER-MARK", body="OTHER-MARK unrelated")
    )
    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_llm_gateway] = lambda: gateway
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/internal/worker/tasks/{task.task_id}/gui/plan",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}"},
            json={"fencing_token": claim["fencing_token"]},
        )
    get_settings.cache_clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["expected_text"] == "ANCOS_LIVE_OK"
    assert gateway.calls == []
