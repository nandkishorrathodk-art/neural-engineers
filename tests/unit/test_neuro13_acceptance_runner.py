import httpx
import pytest
from neural.acceptance.neuro13 import (
    AcceptanceFailed,
    Neuro13AcceptanceRunner,
    validate_terminal_acceptance_plan,
    validate_verified_task,
)


def _valid_verified_task():
    source_hash = "a" * 64
    test_hash = "b" * 64
    checks = [
        "acceptance_criteria_is_supported_test_only_contract",
        "worker_test_result_and_independent_rerun_passed",
        "complete_task_start_workspace_baseline_captured",
        "complete_verification_snapshot_and_workspace_delta_captured",
    ]
    return {
        "task_id": "TASK-REAL-RECEIPT",
        "status": "completed",
        "verified": True,
        "execution_mode": "terminal",
        "acceptance_criteria": "tests pass",
        "assigned_employee_id": "ANC-EMP-6A2B",
        "attempts": [{
            "attempt_id": "attempt-1",
            "worker_id": "worker-1",
            "status": "verified",
            "verification_passed": True,
            "verifier_id": "verifier-1",
            "execution_model": {"provider": "openai", "model": "gpt-4.1-mini"},
        }],
        "verification_evidence": {
            "passed": True,
            "checks": [{"name": name, "passed": True} for name in checks],
            "independent_test_runs": [{
                "executable": "python3",
                "worker_exit_code": 0,
                "verifier_exit_code": 0,
                "evidence_ref": "verifier:attempt-1:test:1",
            }],
            "workspace_manifest_before": {"complete": True, "file_count": 0},
            "workspace_manifest": {
                "complete": True,
                "file_count": 2,
                "returned_file_count": 2,
                "files": [
                    {"path": "src/main.py", "size_bytes": 10, "sha256": source_hash},
                    {"path": "tests/test_main.py", "size_bytes": 20, "sha256": test_hash},
                ],
            },
            "workspace_delta": {
                "complete": True,
                "created": [
                    {"path": "src/main.py", "size_bytes": 10, "sha256": source_hash},
                    {"path": "tests/test_main.py", "size_bytes": 20, "sha256": test_hash},
                ],
                "modified": [],
            },
            "evidence_refs": [f"workspace:sha256:{source_hash}"],
        },
    }


def test_neuro13_runner_rejects_completion_without_independent_receipt():
    with pytest.raises(AcceptanceFailed, match="not independently verified"):
        validate_verified_task({
            "task_id": "TASK-CLAIMED-COMPLETE",
            "status": "completed",
            "verified": False,
            "execution_mode": "terminal",
            "acceptance_criteria": "tests pass",
        })


def test_neuro13_acceptance_plan_keeps_implementation_and_tests_in_one_task():
    with pytest.raises(AcceptanceFailed, match="requires one task"):
        validate_terminal_acceptance_plan([
            {"task_id": "TASK-SOURCE", "execution_mode": "terminal", "acceptance_criteria": "tests pass"},
            {"task_id": "TASK-TESTS", "execution_mode": "terminal", "acceptance_criteria": "tests pass"},
        ])

    tasks = [{"task_id": "TASK-ATOMIC", "execution_mode": "terminal", "acceptance_criteria": "tests pass"}]
    assert validate_terminal_acceptance_plan(tasks) == tasks


def test_neuro13_runner_rejects_a_model_stub_even_if_task_claims_verified():
    with pytest.raises(AcceptanceFailed, match="simulated, fallback, or stub"):
        validate_verified_task({
            "task_id": "TASK-FAKE-MODEL-RECEIPT",
            "status": "completed",
            "verified": True,
            "execution_mode": "terminal",
            "acceptance_criteria": "tests pass",
            "attempts": [{
                "attempt_id": "attempt-1",
                "worker_id": "worker-1",
                "status": "verified",
                "verification_passed": True,
                "verifier_id": "verification-service",
                "execution_model": {"provider": "mock", "model": "stub-model"},
            }],
        })


def test_neuro13_runner_requires_task_start_delta_and_hashed_artifact_evidence():
    with pytest.raises(AcceptanceFailed, match="persisted passing verifier attempt"):
        validate_verified_task({
            "task_id": "TASK-NO-ATTEMPT",
            "status": "completed",
            "verified": True,
            "execution_mode": "terminal",
            "acceptance_criteria": "tests pass",
            "attempts": [],
        })


def test_neuro13_runner_accepts_real_allowlisted_receipt_and_hash_delta():
    result = validate_verified_task(_valid_verified_task())

    assert result["task_id"] == "TASK-REAL-RECEIPT"
    assert result["verifier_id"] == "verifier-1"
    assert result["test_executables"] == ["python3"]
    assert result["artifact_paths"] == ["src/main.py", "tests/test_main.py"]
    assert len(result["artifact_hashes"]) == 2


def test_neuro13_runner_rejects_delta_hash_that_disagrees_with_snapshot():
    task = _valid_verified_task()
    task["verification_evidence"]["workspace_delta"]["created"][0]["sha256"] = "c" * 64

    with pytest.raises(AcceptanceFailed, match="does not match"):
        validate_verified_task(task)


@pytest.mark.asyncio
async def test_neuro13_objective_post_can_use_long_provider_planning_timeout():
    class Response:
        status_code = 200
        is_error = False

        @staticmethod
        def json():
            return {"id": "project-1"}

    class Client:
        timeout = httpx.Timeout(15.0)
        request_timeout = None

        async def post(self, _path, *, json, timeout):
            self.request_timeout = timeout
            return Response()

    runner = Neuro13AcceptanceRunner.__new__(Neuro13AcceptanceRunner)
    client = Client()
    request_timeout = httpx.Timeout(180.0, connect=3.0)

    result = await runner._post(
        client,
        "/boss/objective",
        {"objective": "Build and test"},
        timeout=request_timeout,
    )

    assert result == {"id": "project-1"}
    assert client.request_timeout is request_timeout
