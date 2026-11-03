import json

import httpx
import pytest
from neural.containers import broker_api
from neural.containers.broker_api import VerificationExecRequest
from neural.workers.verifier import VerificationWorker, replayable_test_commands


def _item(planned_commands: list[list[str]]) -> dict:
    return {
        "task_id": "TASK-VERIFY-1",
        "attempt_id": "attempt-1",
        "fencing_token": 7,
        "execution_mode": "terminal",
        "acceptance_criteria": "Automated tests pass",
        "execution_receipt": {
            "planned_commands": planned_commands,
            "commands": [
                {"command": command, "exit_code": 0, "stdout": "worker run passed", "stderr": ""}
                for command in planned_commands
            ],
        },
    }


def test_replayable_test_commands_filter_out_non_test_commands():
    receipt = {
        "planned_commands": [
            ["python", "-c", "print('not replayed')"],
            ["pytest", "-q"],
            ["pytest", "-q"],
            ["npm", "run", "test"],
            ["curl", "https://example.invalid"],
        ]
    }
    assert replayable_test_commands(receipt) == [["pytest", "-q"], ["npm", "run", "test"]]


@pytest.mark.asyncio
async def test_verifier_reruns_declared_test_and_submits_independent_receipt():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 0, "stdout": "2 passed", "stderr": ""})
        if request.method == "POST" and request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "completed"})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_item([["pytest", "-q"]]))
    await verifier.close()

    assert result == {"task_status": "completed"}
    assert [path.rsplit("/", 1)[-1] for path, _ in seen] == ["exec", "review"]
    review = seen[-1][1]
    assert review["passed"] is True
    assert review["checks"][1]["passed"] is True
    assert review["test_summary"]["independent_test_runs"][0]["verifier_exit_code"] == 0
    assert review["evidence_refs"] == ["verifier:attempt-1:test:1"]


@pytest.mark.asyncio
async def test_verifier_fails_closed_without_replayable_tests():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/review"):
            return httpx.Response(200, json={"task_status": "assigned"})
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://core.test", transport=httpx.MockTransport(handler))
    verifier = VerificationWorker("http://core.test", "verifier-test", "v" * 32, client=client)
    result = await verifier.verify_item(_item([["python", "-c", "print('done')"]]))
    await verifier.close()

    assert result == {"task_status": "assigned"}
    assert len(seen) == 1
    assert seen[0][0].endswith("/review")
    assert seen[0][1]["passed"] is False
    assert seen[0][1]["evidence_refs"] == ["verifier:attempt-1:no-replayable-test"]


@pytest.mark.asyncio
async def test_verifier_rejects_nonzero_independent_test_rerun():
    seen: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(await request.aread()) if request.method == "POST" else {}
        seen.append((request.url.path, body))
        if request.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exit_code": 1, "stdout": "", "stderr": "test failed"})
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
        VerificationExecRequest(employee_id="ANC-EMP-VERIFY", command=["pytest", "-q"])
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
    assert calls["exec"]["cmd"] == ["/usr/bin/timeout", "300s", "pytest", "-q"]
    assert calls["exec"]["environment"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert calls["exec"]["environment"]["HOME"] == "/tmp"
    assert calls["removed"] == {"force": True}


def test_broker_verifier_request_rejects_non_test_commands():
    with pytest.raises(ValueError):
        VerificationExecRequest(employee_id="ANC-EMP-VERIFY", command=["python", "-c", "print(1)"])
