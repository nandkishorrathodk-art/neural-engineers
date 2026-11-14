import base64
import json

import httpx
import pytest
from neural.api.schemas.requests import VerifierCommandRequest
from neural.containers import broker_api
from neural.containers.broker_api import VerificationExecRequest, VerificationManifestRequest
from neural.verification.test_commands import (
    validate_test_plan,
    validate_verify_phase_plan,
    validate_workspace_artifact_plan,
)
from neural.verification.workspace_manifest import build_workspace_delta, valid_workspace_manifest
from neural.workers.verifier import VerificationWorker, replayable_test_commands


def _item(planned_commands: list[list[str]]) -> dict:
    return {
        "task_id": "TASK-VERIFY-1",
        "attempt_id": "attempt-1",
        "fencing_token": 7,
        "execution_mode": "terminal",
        "acceptance_criteria": "Automated tests pass",
        "workspace_manifest_before": {
            "captured_at_unix": 1789999990.0,
            "files": [],
            "file_count": 0,
            "total_size_bytes": 0,
            "complete": True,
            "errors": [],
        },
        "execution_receipt": {
            "planned_commands": planned_commands,
            "commands": [
                {"command": command, "exit_code": 0, "stdout": "worker run passed", "stderr": ""}
                for command in planned_commands
            ],
        },
    }


def _manifest(**overrides) -> dict:
    result = {
        "captured_at_unix": 1790000000.0,
        "files": [{"path": "src/main.py", "size_bytes": 12, "sha256": "a" * 64}],
        "file_count": 1,
        "total_size_bytes": 12,
        "complete": True,
        "errors": [],
        "scope": "verification-time workspace snapshot",
    }
    result.update(overrides)
    return result


@pytest.mark.asyncio
async def test_verifier_worker_reports_runtime_heartbeat():
    seen: list[tuple[str, dict]] = []
    verifier: VerificationWorker

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread())
        seen.append((request.url.path, body))
        verifier._stopping.set()
        return httpx.Response(200, json={"status": "recorded"})

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)

    await verifier._runtime_heartbeat_loop()
    await verifier.close()

    assert seen == [(
        "/internal/verifier/runtime/heartbeat",
        {"instance_id": verifier.instance_id},
    )]


def test_replayable_test_commands_filter_out_non_test_commands():
    receipt = {
        "planned_commands": [
            ["python", "-c", "print('not replayed')"],
            ["pytest", "-q"],
            ["pytest", "-q"],
            ["python3", "-m", "unittest", "-v"],
            ["npm", "run", "test"],
            ["curl", "https://example.invalid"],
        ]
    }
    assert replayable_test_commands(receipt) == [
        ["pytest", "-q"],
        ["python3", "-m", "unittest", "-v"],
        ["npm", "run", "test"],
    ]
    assert VerifierCommandRequest(
        fencing_token=7,
        command=["python3", "-m", "unittest", "-v"],
    ).command == ["python3", "-m", "unittest", "-v"]


def test_test_only_plan_requires_independently_replayable_test_command():
    with pytest.raises(ValueError, match="must include a replayable"):
        validate_test_plan("tests pass", [["python3", "-c", "print('inline test')"]])

    validate_test_plan(
        "tests pass",
        [["python3", "-c", "write_file()"], ["python3", "-m", "unittest", "-v"]],
    )
    validate_test_plan("render the requested page", [["python3", "-c", "print('render')"]])


def test_test_task_with_requested_file_requires_persisted_artifact_plan():
    context = "Create test_clamp.py with clamp and unittest cases in the workspace."
    inline_only = [
        ["python3", "-c", "class TestClamp: pass"],
        ["python3", "-m", "unittest", "-v"],
    ]
    with pytest.raises(ValueError, match="does not persist a requested file"):
        validate_workspace_artifact_plan(context, "tests pass", inline_only)

    wrong_path = [[
        "python3",
        "-c",
        "from pathlib import Path; Path('other.py').write_text('value = 1')",
    ], ["python3", "-m", "unittest", "-v"]]
    with pytest.raises(ValueError, match="does not persist a requested file"):
        validate_workspace_artifact_plan(context, "tests pass", wrong_path)

    persisted = [[
        "python3",
        "-c",
        "from pathlib import Path; Path('test_clamp.py').write_text('value = 1')",
    ], ["python3", "-m", "unittest", "-v"]]
    validate_workspace_artifact_plan(context, "tests pass", persisted)

    open_write = [[
        "python3",
        "-c",
        "open('test_clamp.py', 'w').write('value = 1')",
    ], ["python3", "-m", "unittest", "-v"]]
    validate_workspace_artifact_plan(context, "tests pass", open_write)

    context_manager_write = [[
        "python3",
        "-c",
        "with open('test_clamp.py', 'w') as output: output.write('value = 1')",
    ], ["python3", "-m", "unittest", "-v"]]
    validate_workspace_artifact_plan(context, "tests pass", context_manager_write)


def test_non_file_test_task_does_not_require_a_workspace_write():
    validate_workspace_artifact_plan(
        "Run the existing test suite in the workspace.",
        "tests pass",
        [["python3", "-m", "unittest", "-v"]],
    )


def test_verify_phase_may_not_rewrite_the_artifact():
    """The GUI phase produced handoff.txt; the verify phase must only read it."""
    rewrite = [[
        "python3",
        "-c",
        "open('handoff.txt', 'w').write('ANCOS_HYBRID_TEST\\n')",
    ]]
    with pytest.raises(ValueError, match="must not rewrite an artifact"):
        validate_verify_phase_plan(["handoff.txt"], rewrite)

    read_only = [[
        "python3",
        "-c",
        "from pathlib import Path; Path('test_handoff.py').write_text('def test_x(): pass')",
    ], ["python3", "-m", "pytest", "-q"]]
    validate_verify_phase_plan(["handoff.txt"], read_only)


def test_verify_phase_rejects_pytest_without_a_discoverable_module():
    """A pytest run with no `test_*.py` module collects nothing and exits non-zero."""
    no_module = [[
        "python3",
        "-c",
        "from pathlib import Path; Path('handoff_checker.py').write_text('print(1)')",
    ], ["python3", "-m", "pytest", "-q"]]
    with pytest.raises(ValueError, match="no discoverable"):
        validate_verify_phase_plan(["handoff.txt"], no_module)

    unittest_style = [[
        "python3",
        "-c",
        "from pathlib import Path; Path('checker.py').write_text('import unittest')",
    ], ["python3", "-m", "unittest", "-v"]]
    validate_verify_phase_plan(["handoff.txt"], unittest_style)


def test_verify_phase_rejects_a_fabricated_digest_constant():
    """A checker must compute the hash, not assert an invented constant."""
    invented = "a1" * 32
    fabricated = base64.b64encode(
        f'def test_x():\n    assert True\nEXPECTED = "{invented}"\n'.encode()
    ).decode()
    commands = [[
        "python3",
        "-c",
        f"import base64; open('test_handoff.py','wb').write(base64.b64decode('{fabricated}'))",
    ], ["python3", "-m", "pytest", "-q"]]
    with pytest.raises(ValueError, match="hardcodes a 64-character hex digest"):
        validate_verify_phase_plan(["handoff.txt"], commands)


@pytest.mark.asyncio
async def test_verifier_reruns_declared_test_and_submits_independent_receipt():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 0, "stdout": "2 passed", "stderr": ""})
        if request.method == "POST" and request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest())
        if request.method == "POST" and request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "completed"})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_item([["python3", "-m", "unittest", "-v"]]))
    await verifier.close()

    assert result == {"task_status": "completed"}
    assert [path.rsplit("/", 1)[-1] for path, _ in seen] == ["exec", "manifest", "review"]
    assert seen[1][1] == {"fencing_token": 7}
    review = seen[-1][1]
    assert review["passed"] is True
    assert review["checks"][1]["passed"] is True
    assert review["test_summary"]["independent_test_runs"][0]["verifier_exit_code"] == 0
    assert review["test_summary"]["workspace_manifest"]["files"][0]["sha256"] == "a" * 64
    assert review["checks"][2]["passed"] is True
    assert review["checks"][3]["passed"] is True
    assert review["test_summary"]["workspace_delta"]["created"][0]["path"] == "src/main.py"
    assert review["evidence_refs"][:2] == ["verifier:attempt-1:test:1", f"workspace:sha256:{'a' * 64}"]
    assert any(reference.startswith("workspace-baseline:sha256:") for reference in review["evidence_refs"])
    assert any(reference.startswith("workspace-delta:sha256:") for reference in review["evidence_refs"])


@pytest.mark.asyncio
async def test_verifier_fails_closed_without_replayable_tests():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "assigned"})
        if request.method == "POST" and request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(files=[], file_count=0, total_size_bytes=0))
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_item([["python", "-c", "print('done')"]]))
    await verifier.close()

    assert result == {"task_status": "assigned"}
    assert [path.rsplit("/", 1)[-1] for path, _ in seen] == ["manifest", "review"]
    assert seen[-1][1]["passed"] is False
    assert seen[-1][1]["evidence_refs"][0] == "verifier:attempt-1:no-replayable-test"


@pytest.mark.asyncio
async def test_verifier_rejects_nonzero_independent_test_rerun():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 1, "stdout": "", "stderr": "test failed"})
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest())
        if request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "assigned"})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    await verifier.verify_item(_item([["pytest", "-q"]]))
    await verifier.close()

    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][1]["passed"] is False
    assert review["test_summary"]["independent_test_runs"][0]["verifier_exit_code"] == 1


@pytest.mark.asyncio
async def test_verifier_rejects_acceptance_criteria_beyond_test_only_contract():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 0, "stdout": "2 passed", "stderr": ""})
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest())
        if request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "assigned"})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    item = _item([["pytest", "-q"]])
    item["acceptance_criteria"] = "Automated tests pass and deliver artifact evidence"
    await verifier.verify_item(item)
    await verifier.close()

    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][0]["passed"] is False


@pytest.mark.asyncio
async def test_verifier_fails_closed_for_incomplete_manifest():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread())
        seen.append((request.url.path, body))
        if request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 0, "stdout": "passed", "stderr": ""})
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(complete=False, errors=["size limit exceeded"]))
        if request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "assigned"})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    await verifier.verify_item(_item([["pytest", "-q"]]))
    await verifier.close()
    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][3]["passed"] is False
    assert review["test_summary"]["workspace_manifest"]["complete"] is False


@pytest.mark.asyncio
async def test_verifier_fails_closed_when_task_start_baseline_is_missing():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 0, "stdout": "passed", "stderr": ""})
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest())
        if request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "assigned"})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    item = _item([["pytest", "-q"]])
    item.pop("workspace_manifest_before")
    await verifier.verify_item(item)
    await verifier.close()

    review = seen[-1][1]
    assert review["passed"] is False
    assert review["checks"][2]["passed"] is False
    assert review["checks"][3]["passed"] is False
    assert review["test_summary"]["workspace_delta"]["complete"] is False


def test_workspace_manifest_validation_and_delta_are_fail_closed():
    empty_baseline = {
        "captured_at_unix": 1789999990.0,
        "files": [], "file_count": 0, "total_size_bytes": 0,
        "complete": True, "errors": [],
    }
    assert valid_workspace_manifest(empty_baseline, allow_empty=True)
    assert not valid_workspace_manifest(empty_baseline)
    assert not valid_workspace_manifest(_manifest(files=[{"path": "../secret", "size_bytes": 1, "sha256": "a" * 64}]))
    assert not valid_workspace_manifest(_manifest(files=[
        {"path": "a.txt", "size_bytes": 1, "sha256": "a" * 64},
        {"path": "a.txt", "size_bytes": 1, "sha256": "a" * 64},
    ], file_count=2, total_size_bytes=2))

    before = _manifest(files=[
        {"path": "keep.txt", "size_bytes": 1, "sha256": "a" * 64},
        {"path": "change.txt", "size_bytes": 1, "sha256": "b" * 64},
        {"path": "remove.txt", "size_bytes": 1, "sha256": "c" * 64},
    ], file_count=3, total_size_bytes=3)
    after = _manifest(files=[
        {"path": "keep.txt", "size_bytes": 1, "sha256": "a" * 64},
        {"path": "change.txt", "size_bytes": 2, "sha256": "d" * 64},
        {"path": "new.txt", "size_bytes": 1, "sha256": "e" * 64},
    ], file_count=3, total_size_bytes=4)
    delta = build_workspace_delta(before, after)
    assert delta["counts"] == {"created": 1, "modified": 1, "deleted": 1}
    assert delta["created"][0]["path"] == "new.txt"
    assert delta["modified"][0]["path"] == "change.txt"
    assert delta["deleted"][0]["path"] == "remove.txt"


def test_broker_runs_verification_in_read_only_offline_workspace(monkeypatch):
    calls: dict = {}

    class VerifierContainer:
        def exec_run(self, **kwargs):
            calls["exec"] = kwargs
            return type("ExecResult", (), {"exit_code": 0, "output": b"3 passed"})()

        def remove(self, **kwargs):
            calls["removed"] = kwargs

    class ContainerCollection:
        def run(self, **kwargs):
            calls["run"] = kwargs
            return VerifierContainer()

    class VolumeCollection:
        def get(self, _name):
            return type("Workspace", (), {
                "attrs": {"Labels": {"ancos.managed": "true", "ancos.employee_id": "ANC-EMP-VERIFY"}}
            })()

    fake_client = type("DockerClient", (), {
        "containers": ContainerCollection(),
        "volumes": VolumeCollection(),
    })()
    workstation = type("Workstation", (), {
        "labels": {
            "ancos.employee_id": "ANC-EMP-VERIFY",
            "ancos.workspace_volume": "ancos-workspace-anc-emp-verify",
        },
        "image": type("Image", (), {"tags": ["ancos/employee-base:latest"]})(),
    })()
    monkeypatch.setattr(broker_api, "docker_client", lambda: fake_client)
    monkeypatch.setattr(broker_api, "get_managed_container", lambda _name: workstation)

    result = broker_api.execute_verifier_test(
        VerificationExecRequest(
            employee_id="ANC-EMP-VERIFY",
            command=["python3", "-m", "unittest", "-v"],
        )
    )

    assert result == {"exit_code": 0, "stdout": "3 passed", "stderr": ""}
    run = calls["run"]
    assert run["volumes"] == {"ancos-workspace-anc-emp-verify": {
        "bind": "/home/employee/workspace", "mode": "ro"
    }}
    assert run["network_disabled"] is True
    assert run["read_only"] is True
    assert run["entrypoint"] == ["/bin/sleep"]
    assert run["cap_drop"] == ["ALL"]
    assert run["security_opt"] == ["no-new-privileges:true"]
    assert calls["exec"]["cmd"] == [
        "/usr/bin/timeout", "300s", "python3", "-m", "unittest", "-v"
    ]
    assert calls["exec"]["environment"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert calls["exec"]["environment"]["HOME"] == "/tmp"
    assert calls["removed"] == {"force": True}


def test_broker_captures_bounded_manifest_through_read_only_sandbox(monkeypatch):
    calls: dict = {}

    class VerifierContainer:
        def exec_run(self, **kwargs):
            calls["exec"] = kwargs
            return type("ExecResult", (), {"exit_code": 0, "output": json.dumps(_manifest()).encode()})()

        def remove(self, **kwargs):
            calls["removed"] = kwargs

    class ContainerCollection:
        def run(self, **kwargs):
            calls["run"] = kwargs
            return VerifierContainer()

    class VolumeCollection:
        def get(self, _name):
            return type("Workspace", (), {
                "attrs": {"Labels": {"ancos.managed": "true", "ancos.employee_id": "ANC-EMP-VERIFY"}}
            })()

    fake_client = type("DockerClient", (), {
        "containers": ContainerCollection(), "volumes": VolumeCollection()
    })()
    workstation = type("Workstation", (), {
        "labels": {
            "ancos.employee_id": "ANC-EMP-VERIFY",
            "ancos.workspace_volume": "ancos-workspace-anc-emp-verify",
        },
        "image": type("Image", (), {"tags": ["ancos/employee-base:latest"]})(),
    })()
    monkeypatch.setattr(broker_api, "docker_client", lambda: fake_client)
    monkeypatch.setattr(broker_api, "get_managed_container", lambda _name: workstation)

    manifest = broker_api.capture_verification_manifest(
        VerificationManifestRequest(employee_id="ANC-EMP-VERIFY")
    )

    assert manifest["complete"] is True
    assert manifest["files"][0]["sha256"] == "a" * 64
    assert calls["run"]["volumes"]["ancos-workspace-anc-emp-verify"]["mode"] == "ro"
    assert calls["run"]["network_disabled"] is True
    assert calls["exec"]["cmd"][:6] == [
        "/usr/bin/timeout",
        "300s",
        "python3",
        "-I",
        "-B",
        "-c",
    ]
    assert "max_files=2000" in calls["exec"]["cmd"][-1]
    assert calls["removed"] == {"force": True}


def test_broker_verifier_request_rejects_non_test_commands():
    with pytest.raises(ValueError):
        VerificationExecRequest(employee_id="ANC-EMP-VERIFY", command=["python", "-c", "print(1)"])
