import pytest
from neural.constants import TaskStatus, VerificationStatus
from neural.db.repositories import EmployeeRepository, ProjectRepository, TaskRepository
from neural.employees.factory import EmployeeFactory
from neural.evaluation.benchmarks import (
    BenchmarkRunner,
)
from neural.evaluation.verifier import IndependentVerifier
from neural.exceptions import UnverifiedTaskError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_verification_passing_receipt(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)
    emp = await factory.hire_employee(name="Alice Tech", title="Backend Engineer", initial_skills=["Python", "FastAPI"])

    verifier = IndependentVerifier(employee_repo=emp_repo)
    result = await verifier.verify_task_result(
        task_id="TASK-VERIFY-01",
        employee_id=emp.employee_id,
        artifacts=["/workspace/app.py"],
        test_exit_code=0,
        tested_skills=["Python", "FastAPI"],
        file_contents={"/workspace/app.py": "def add(a, b):\n    return a + b\n"}
    )

    assert result["passed"] is True
    assert result["verification_status"] == VerificationStatus.PASSED.value
    assert "Python" in result["verified_skills"]
    assert "FastAPI" in result["verified_skills"]
    assert "receipt" in result
    assert len(result["signature"]) == 64


@pytest.mark.asyncio
async def test_verification_failing_exit_code_and_diagnostic_feedback(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)
    emp = await factory.hire_employee(name="Bob Coder", title="Software Engineer", initial_skills=["Python"])

    verifier = IndependentVerifier(employee_repo=emp_repo)
    result = await verifier.verify_task_result(
        task_id="TASK-FAIL-01",
        employee_id=emp.employee_id,
        artifacts=["/workspace/app.py"],
        test_exit_code=1,
        tested_skills=["Python"],
        file_contents={"/workspace/app.py": "def test(): pass\n"}
    )

    assert result["passed"] is False
    assert result["verification_status"] == VerificationStatus.FAILED.value
    assert len(result["verified_skills"]) == 0
    assert "failed with exit code 1" in result["notes"].lower()


@pytest.mark.asyncio
async def test_verification_code_quality_ast_syntax_error(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    verifier = IndependentVerifier(employee_repo=emp_repo)

    # Broken syntax code
    broken_code = "def broken_func(\n    return 42\n"
    quality = verifier.check_code_quality(
        artifacts=["/workspace/broken.py"],
        file_contents={"/workspace/broken.py": broken_code}
    )

    assert quality["passed"] is False
    assert quality["syntax_valid"] is False
    assert quality["quality_score"] < 1.0
    assert any(i["type"] == "syntax_error" for i in quality["issues"])


@pytest.mark.asyncio
async def test_verification_code_quality_security_violation(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    verifier = IndependentVerifier(employee_repo=emp_repo)

    # Insecure eval construct
    insecure_code = "def run_input(user_str):\n    return eval(user_str)\n"
    quality = verifier.check_code_quality(
        artifacts=["/workspace/vuln.py"],
        file_contents={"/workspace/vuln.py": insecure_code}
    )

    assert quality["passed"] is False
    assert quality["security_passed"] is False
    assert any(i["type"] == "security_violation" for i in quality["issues"])


@pytest.mark.asyncio
async def test_strict_gate_task_completion_without_receipt_blocked(async_session: AsyncSession):
    """Strict Gate: Task cannot transition to status='completed' without independent verification receipt."""
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session, strict_verification=True)
    project = await proj_repo.create(name="Project-Gate-Test", objective="Gate testing")

    task = await task_repo.create(
        task_id="TASK-UNVERIFIED-GATE",
        project_id=project.id,
        title="Unverified task",
        objective="Must not complete without QA receipt",
        scope="Gate",
        acceptance_criteria="Pass verification",
        status=TaskStatus.RUNNING
    )

    # Attempting to directly update status to COMPLETED with strict_verification enabled MUST raise UnverifiedTaskError!
    with pytest.raises(UnverifiedTaskError) as exc_info:
        await task_repo.update_status(task.task_id, TaskStatus.COMPLETED)
    assert "strict gate violation" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_strict_gate_with_verifier_and_tamper_detection(async_session: AsyncSession):
    proj_repo = ProjectRepository(async_session)
    task_repo = TaskRepository(async_session)
    emp_repo = EmployeeRepository(async_session)
    project = await proj_repo.create(name="Project-Tamper-Test", objective="Tamper test")

    task = await task_repo.create(
        task_id="TASK-GATE-VERIFY-01",
        project_id=project.id,
        title="Gate test task",
        objective="Verify with receipt",
        scope="Testing",
        acceptance_criteria="Green tests",
        status=TaskStatus.RUNNING
    )

    verifier = IndependentVerifier(employee_repo=emp_repo, task_repo=task_repo)

    # 1. Verification with failing receipt is rejected
    failing_receipt = {"passed": False, "verification_status": "failed"}
    with pytest.raises(UnverifiedTaskError):
        await verifier.gate_task_completion(task.task_id, failing_receipt)

    # 2. Verification with passing receipt succeeds
    v_res = await verifier.verify_task_result(
        task_id=task.task_id,
        employee_id="ANC-EMP-TEST",
        artifacts=["/workspace/ok.py"],
        test_exit_code=0,
        tested_skills=["Python"],
        file_contents={"/workspace/ok.py": "x = 1\n"}
    )
    receipt = v_res["receipt"]

    completed_task = await verifier.gate_task_completion(task.task_id, receipt)
    assert completed_task.status == TaskStatus.COMPLETED
    assert completed_task.completed_at is not None
    assert completed_task.verification_result["passed"] is True

    # 3. Tampered receipt signature is blocked
    tampered_receipt = dict(receipt)
    tampered_receipt["signature"] = "tampered_fake_signature_hex"
    with pytest.raises(UnverifiedTaskError) as exc_info:
        await verifier.gate_task_completion(task.task_id, tampered_receipt)
    assert "signature mismatch" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_empirical_skill_certification_lifecycle(async_session: AsyncSession):
    """Empirical certification: skills transition verified=False -> verified=True with task evidence."""
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(
        name="Diana Prince",
        title="Senior Security Architect",
        initial_skills=["System Design", "Docker"]
    )

    # Before verification: all skills unverified
    assert all(not s.verified for s in emp.skills)
    assert await emp_repo.has_verified_skill(emp.employee_id, "System Design") is False

    verifier = IndependentVerifier(employee_repo=emp_repo)
    v_result = await verifier.verify_task_result(
        task_id="TASK-EVIDENCE-101",
        employee_id=emp.employee_id,
        artifacts=["/workspace/arch.md"],
        test_exit_code=0,
        tested_skills=["System Design", "Docker", "Kubernetes"]  # Kubernetes is newly certified!
    )
    assert v_result["passed"] is True

    # Re-fetch employee from DB
    refreshed_emp = await emp_repo.get_by_employee_id(emp.employee_id)
    verified_names = await emp_repo.list_verified_skills(emp.employee_id)

    assert "System Design" in verified_names
    assert "Docker" in verified_names
    assert "Kubernetes" in verified_names
    assert await emp_repo.has_verified_skill(emp.employee_id, "Kubernetes") is True

    # Check evidence link
    k8s_skill = next(s for s in refreshed_emp.skills if s.skill_name == "Kubernetes")
    assert k8s_skill.verified is True
    assert "TASK-EVIDENCE-101" in k8s_skill.evidence_task_ids


def test_standard_skill_benchmarks_and_runner():
    runner = BenchmarkRunner()

    # 1. Python Backend Benchmark
    py_bench = runner.get_benchmark("Python")
    assert py_bench is not None

    def candidate_py(inp):
        if isinstance(inp, list) and isinstance(inp[0], int):
            return [x**2 for x in inp if x % 2 == 0]
        if isinstance(inp, list) and isinstance(inp[0], dict):
            res = {}
            for item in inp:
                res[item["cat"]] = res.get(item["cat"], 0) + item["val"]
            return res
        return inp

    py_res = py_bench.run(candidate_py)
    assert py_res.passed is True
    assert py_res.pass_rate == 1.0
    assert py_res.quality_score == 1.0

    # 2. Failing candidate on benchmark
    def failing_py(inp):
        return []

    failing_res = py_bench.run(failing_py)
    assert failing_res.passed is False
    assert failing_res.pass_rate == 0.0

    # 3. A/B comparison and regression detection
    comparison = runner.compare_a_b(py_res, failing_res)
    assert comparison["is_regression"] is True
    assert comparison["verdict"] == "regression"
    assert comparison["pass_rate_delta"] < 0
