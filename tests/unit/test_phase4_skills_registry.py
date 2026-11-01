import pytest
from neural.constants import SkillLevel
from neural.db.repositories import EmployeeRepository, SkillCertificationRepository
from neural.employees.factory import EmployeeFactory
from neural.skills.certification import EmpiricalCertificationEngine
from neural.skills.registry import (
    GradingRubric,
    RubricCriterion,
    get_skill_registry,
)
from sqlalchemy.ext.asyncio import AsyncSession


def test_skill_registry_catalog_bootstrap():
    registry = get_skill_registry()
    skills = registry.list_skills()
    assert len(skills) >= 6

    py_skill = registry.get_skill("Python")
    assert py_skill is not None
    assert py_skill.category == "Backend"
    assert "novice" in py_skill.benchmarks
    assert "competent" in py_skill.benchmarks
    assert "expert" in py_skill.benchmarks
    assert "master" in py_skill.benchmarks

    qa_skill = registry.get_skill("pytest")
    assert qa_skill is not None
    assert qa_skill.category == "QA"

    sec_skill = registry.get_skill("Security Auditing")
    assert sec_skill is not None
    assert sec_skill.category == "Security"


def test_grading_rubric_evaluation():
    rubric = GradingRubric(
        criteria=[
            RubricCriterion("functional_correctness", 0.50, 0.80, "Test suite assertions"),
            RubricCriterion("code_quality", 0.30, 0.70, "Code structure and AST"),
            RubricCriterion("security_compliance", 0.20, 0.80, "Security rules"),
        ],
        min_pass_score=0.80,
    )

    # 1. Passing scores
    passed, score, eval_dict = rubric.evaluate({
        "functional_correctness": 1.0,
        "code_quality": 0.9,
        "security_compliance": 1.0,
    })
    assert passed is True
    assert score >= 0.9
    assert eval_dict["passed"] is True

    # 2. Failing one criterion below min_score
    passed_fail, score_fail, eval_dict_fail = rubric.evaluate({
        "functional_correctness": 0.50,  # below min_score 0.80
        "code_quality": 1.0,
        "security_compliance": 1.0,
    })
    assert passed_fail is False
    assert eval_dict_fail["criteria"]["functional_correctness"]["passed"] is False


@pytest.mark.asyncio
async def test_empirical_certification_passing_lifecycle(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    cert_repo = SkillCertificationRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    # Hire employee with unverified initial skills
    emp = await factory.hire_employee(
        name="Elena Rostova",
        title="Software Engineer",
        initial_skills=["Python"],
    )
    assert not emp.skills[0].verified

    engine = EmpiricalCertificationEngine(
        cert_repo=cert_repo,
        employee_repo=emp_repo,
        registry=get_skill_registry(),
    )

    # Valid candidate code solving Novice Python benchmark
    solution_code = """
def solve(items):
    return [x**2 for x in items if x % 2 == 0]
"""

    res = await engine.certify_skill(
        employee_id=emp.employee_id,
        skill_name="Python",
        target_level=SkillLevel.NOVICE,
        code_submission=solution_code,
    )

    assert res.passed is True
    assert res.score >= 0.80
    assert res.certificate_id is not None
    assert len(res.certificate_hash) == 64
    assert res.receipt["exit_code"] == 0

    # Verify certificate record exists in database
    cert_db = await cert_repo.get_by_certificate_id(res.certificate_id)
    assert cert_db is not None
    assert cert_db.skill_name == "Python"
    assert cert_db.level == SkillLevel.NOVICE

    # Verify employee skill profile was upgraded and verified!
    reloaded_emp = await emp_repo.get_by_employee_id(emp.employee_id)
    py_skill = next(s for s in reloaded_emp.skills if s.skill_name == "Python")
    assert py_skill.verified is True
    assert py_skill.level == SkillLevel.NOVICE
    assert res.certificate_id in py_skill.evidence_task_ids


@pytest.mark.asyncio
async def test_empirical_certification_rejection_zero_success_by_decree(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    cert_repo = SkillCertificationRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(name="Viktor Coder", title="Junior Dev", initial_skills=["Python"])

    engine = EmpiricalCertificationEngine(cert_repo=cert_repo, employee_repo=emp_repo)

    # Broken/failing solution
    buggy_code = """
def solve(items):
    return []  # completely incorrect output
"""

    res = await engine.certify_skill(
        employee_id=emp.employee_id,
        skill_name="Python",
        target_level=SkillLevel.NOVICE,
        code_submission=buggy_code,
    )

    # Must fail! Zero success-by-decree
    assert res.passed is False
    assert res.certificate_id is None
    assert "below threshold" in res.notes.lower() or "failed" in res.notes.lower()

    # Verify skill remains unverified
    reloaded_emp = await emp_repo.get_by_employee_id(emp.employee_id)
    py_skill = next(s for s in reloaded_emp.skills if s.skill_name == "Python")
    assert py_skill.verified is False


@pytest.mark.asyncio
async def test_empirical_certification_blocks_security_violation(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    cert_repo = SkillCertificationRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(name="Malicious Actor", title="Intern", initial_skills=["Python"])

    engine = EmpiricalCertificationEngine(cert_repo=cert_repo, employee_repo=emp_repo)

    # Insecure eval construct in submission
    insecure_code = """
def solve(items):
    return eval(str(items))
"""

    res = await engine.certify_skill(
        employee_id=emp.employee_id,
        skill_name="Python",
        target_level=SkillLevel.NOVICE,
        code_submission=insecure_code,
    )

    assert res.passed is False
    assert "security" in res.notes.lower()


@pytest.mark.asyncio
async def test_certificate_cryptographic_hash_integrity(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    cert_repo = SkillCertificationRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(name="Grace Hopper", title="Principal Engineer", initial_skills=["Python"])

    # Create certificate
    cert = await cert_repo.create_certification(
        certificate_id="CERT-TEST-INTEGRITY-01",
        employee_id=emp.id,
        skill_name="Python",
        level=SkillLevel.MASTER,
        score=0.98,
        benchmark_id="BENCH-PY-MST-01",
    )

    # 1. Valid hash check
    is_valid, msg = await cert_repo.verify_certificate_integrity(cert.certificate_id)
    assert is_valid is True
    assert "verified" in msg.lower()

    # 2. Tampered hash check
    cert.certificate_hash = "tampered_fake_sha256_hash_value"
    await async_session.commit()

    is_valid_tampered, msg_tampered = await cert_repo.verify_certificate_integrity(cert.certificate_id)
    assert is_valid_tampered is False
    assert "mismatch" in msg_tampered.lower()


def test_evaluate_security_catches_eval_with_spaces_and_credentials():
    # 1. eval with space before parenthesis
    code_eval = "def solve(x):\n    return eval (x)\n"
    s1, v1 = EmpiricalCertificationEngine._evaluate_security(code_eval)
    assert s1 < 1.0
    assert any("eval" in v.lower() for v in v1)

    # 2. exec with space
    code_exec = "def solve(x):\n    return exec (x)\n"
    s2, v2 = EmpiricalCertificationEngine._evaluate_security(code_exec)
    assert s2 < 1.0
    assert any("exec" in v.lower() for v in v2)

    # 3. os.system
    code_os = "import os\ndef solve(x):\n    os.system('id')\n    return x\n"
    s3, v3 = EmpiricalCertificationEngine._evaluate_security(code_os)
    assert s3 < 1.0
    assert any("os.system" in v.lower() for v in v3)

    # 4. subprocess.run
    code_sub = "import subprocess\ndef solve(x):\n    subprocess.run(['ls'])\n    return x\n"
    s4, v4 = EmpiricalCertificationEngine._evaluate_security(code_sub)
    assert s4 < 1.0
    assert any("subprocess" in v.lower() for v in v4)

    # 5. Hardcoded double-quoted secret
    code_pass = 'def solve(x):\n    password = "SuperSecret123"\n    return x\n'
    s5, v5 = EmpiricalCertificationEngine._evaluate_security(code_pass)
    assert s5 < 1.0
    assert any("credentials" in v.lower() or "secret" in v.lower() for v in v5)


@pytest.mark.asyncio
async def test_empirical_certification_rejects_empty_code(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    cert_repo = SkillCertificationRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(name="Empty Submitter", title="Dev", initial_skills=["Python"])
    engine = EmpiricalCertificationEngine(cert_repo=cert_repo, employee_repo=emp_repo)

    with pytest.raises(ValueError, match="cannot be empty"):
        await engine.certify_skill(
            employee_id=emp.employee_id,
            skill_name="Python",
            target_level=SkillLevel.NOVICE,
            code_submission="   \n  ",
        )


@pytest.mark.asyncio
async def test_empirical_certification_rejects_failing_assertions_even_if_rubric_passes(async_session: AsyncSession):
    emp_repo = EmployeeRepository(async_session)
    cert_repo = SkillCertificationRepository(async_session)
    factory = EmployeeFactory(emp_repo)

    emp = await factory.hire_employee(name="Partial Solver", title="Dev", initial_skills=["Python"])
    engine = EmpiricalCertificationEngine(cert_repo=cert_repo, employee_repo=emp_repo)

    # A custom test runner where 8 out of 10 tests passed (so functional_score = 0.80),
    # but passed is False. Zero success-by-decree dictates this MUST NOT be certified!
    failing_test_runner = """import json, sys
print(json.dumps({"passed": False, "passed_tests": 8, "total_tests": 10}))
sys.exit(0)
"""

    res = await engine.certify_skill(
        employee_id=emp.employee_id,
        skill_name="Python",
        target_level=SkillLevel.NOVICE,
        code_submission="def solve(x):\n    return x\n",
        test_override=failing_test_runner,
    )

    assert res.passed is False
    assert res.certificate_id is None
    assert "assertions failed" in res.notes.lower()

