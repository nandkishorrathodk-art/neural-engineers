from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from neural.api.deps import (
    get_employee_repo,
    get_skill_cert_repo,
)
from neural.api.schemas.requests import StartCertificationRequest
from neural.api.schemas.responses import (
    BenchmarkItemResponse,
    CertificateDetailResponse,
    CertificateVerificationResponse,
    CertificationResponse,
    EmployeeProgressionResponse,
    SkillCatalogItemResponse,
    SkillMatrixResponse,
)
from neural.db.models import SkillCertification
from neural.db.repositories import EmployeeRepository, SkillCertificationRepository
from neural.evolution.progression import SkillProgressionTracker
from neural.skills.certification import EmpiricalCertificationEngine
from neural.skills.registry import SkillRegistry, get_skill_registry

router = APIRouter(prefix="/skills", tags=["Skills & Empirical Certification"])


def serialize_certificate(c: SkillCertification) -> CertificateDetailResponse:
    level_str = c.level.value if hasattr(c.level, "value") else str(c.level)
    return CertificateDetailResponse(
        id=c.id,
        certificate_id=c.certificate_id,
        employee_id=c.employee_id,
        skill_name=c.skill_name,
        level=level_str,
        score=c.score,
        status=c.status,
        benchmark_id=c.benchmark_id,
        certificate_hash=c.certificate_hash,
        execution_receipt=c.execution_receipt or {},
        rubric_scores=c.rubric_scores or {},
        issued_at=c.issued_at,
        expires_at=c.expires_at,
    )


@router.get("/catalog", response_model=list[SkillCatalogItemResponse])
async def list_skill_catalog(
    category: str | None = Query(None, description="Filter by category (Backend, QA, Security, DevOps, Architecture)"),
    registry: SkillRegistry = Depends(get_skill_registry),
):
    """Lists verified skill catalog with grading rubrics and available benchmark tiers."""
    skills = registry.list_skills(category=category)
    return [
        SkillCatalogItemResponse(
            skill_id=s.skill_id,
            name=s.name,
            category=s.category,
            description=s.description,
            grading_rubric=s.grading_rubric.to_dict(),
            available_levels=list(s.benchmarks.keys()),
        )
        for s in skills
    ]


@router.get("/catalog/{skill_name}", response_model=SkillCatalogItemResponse)
async def get_skill_catalog_entry(
    skill_name: str,
    registry: SkillRegistry = Depends(get_skill_registry),
):
    """Retrieves single skill catalog specification by name or ID."""
    skill = registry.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found in catalog")
    return SkillCatalogItemResponse(
        skill_id=skill.skill_id,
        name=skill.name,
        category=skill.category,
        description=skill.description,
        grading_rubric=skill.grading_rubric.to_dict(),
        available_levels=list(skill.benchmarks.keys()),
    )


@router.get("/benchmarks", response_model=list[BenchmarkItemResponse])
async def list_benchmarks(
    skill_name: str | None = Query(None, description="Filter by skill name"),
    level: str | None = Query(None, description="Filter by level tier (novice, competent, expert, master)"),
    registry: SkillRegistry = Depends(get_skill_registry),
):
    """Lists available empirical skill benchmarks."""
    benchmarks = registry.list_benchmarks(skill_name=skill_name, level=level)
    return [
        BenchmarkItemResponse(
            benchmark_id=b.benchmark_id,
            skill_name=b.skill_name,
            level=b.level.value if hasattr(b.level, "value") else str(b.level),
            title=b.title,
            description=b.description,
            test_cases_count=len(b.test_cases),
        )
        for b in benchmarks
    ]


@router.get("/benchmarks/{benchmark_id}")
async def get_benchmark_detail(
    benchmark_id: str,
    registry: SkillRegistry = Depends(get_skill_registry),
):
    """Retrieves full benchmark challenge details and test suite."""
    for s in registry.list_skills():
        for b in s.benchmarks.values():
            if b.benchmark_id.lower() == benchmark_id.lower():
                return b.to_dict()
    raise HTTPException(status_code=404, detail=f"Benchmark '{benchmark_id}' not found")


@router.post("/certify", response_model=CertificationResponse)
async def start_skill_certification(
    req: StartCertificationRequest,
    cert_repo: SkillCertificationRepository = Depends(get_skill_cert_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    registry: SkillRegistry = Depends(get_skill_registry),
):
    """
    Executes empirical skill certification test inside isolated workstation sandbox.
    Zero success-by-decree: requires passing sandbox execution receipts, AST quality, and rubrics.
    On pass: generates cryptographic SHA-256 certificate and upgrades employee profile.
    """
    if not req.code_submission or not req.code_submission.strip():
        raise HTTPException(status_code=400, detail="Code submission cannot be empty")

    try:
        engine = EmpiricalCertificationEngine(
            cert_repo=cert_repo,
            employee_repo=employee_repo,
            registry=registry,
        )
        result = await engine.certify_skill(
            employee_id=req.employee_id,
            skill_name=req.skill_name,
            target_level=req.target_level,
            code_submission=req.code_submission,
            test_override=req.test_override,
            timeout=req.timeout,
        )
        return CertificationResponse(
            passed=result.passed,
            certificate_id=result.certificate_id,
            certificate_hash=result.certificate_hash,
            skill_name=result.skill_name,
            target_level=result.target_level,
            employee_id=result.employee_id,
            score=result.score,
            rubric_evaluation=result.rubric_evaluation,
            receipt=result.receipt,
            notes=result.notes,
        )
    except ValueError as val_err:
        detail = str(val_err)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Certification execution error: {ex!s}")


@router.get("/certificates", response_model=list[CertificateDetailResponse])
async def list_certificates(
    employee_id: str | None = Query(None, description="Filter by employee ID"),
    skill_name: str | None = Query(None, description="Filter by skill name"),
    status: str | None = Query(None, description="Filter by status (certified, revoked, expired)"),
    cert_repo: SkillCertificationRepository = Depends(get_skill_cert_repo),
):
    """Lists issued skill certificates with cryptographic hash anchors."""
    certs = await cert_repo.list_all(
        skill_name=skill_name,
        employee_id=employee_id,
        status=status,
    )
    return [serialize_certificate(c) for c in certs]


@router.get("/certificates/{certificate_id}", response_model=CertificateDetailResponse)
async def get_certificate(
    certificate_id: str,
    cert_repo: SkillCertificationRepository = Depends(get_skill_cert_repo),
):
    """Retrieves single certificate details by certificate ID."""
    cert = await cert_repo.get_by_certificate_id(certificate_id)
    if not cert:
        raise HTTPException(status_code=404, detail=f"Certificate '{certificate_id}' not found")
    return serialize_certificate(cert)


@router.get("/certificates/{certificate_id}/verify", response_model=CertificateVerificationResponse)
async def verify_certificate_integrity(
    certificate_id: str,
    cert_repo: SkillCertificationRepository = Depends(get_skill_cert_repo),
):
    """Verifies cryptographic SHA-256 hash integrity of an issued certificate."""
    cert = await cert_repo.get_by_certificate_id(certificate_id)
    if not cert:
        raise HTTPException(status_code=404, detail=f"Certificate '{certificate_id}' not found")

    is_valid, msg = await cert_repo.verify_certificate_integrity(certificate_id)
    return CertificateVerificationResponse(
        certificate_id=certificate_id,
        is_valid=is_valid,
        message=msg,
        certificate_hash=cert.certificate_hash,
        verified_at=datetime.now(UTC),
    )


@router.get("/matrix", response_model=SkillMatrixResponse)
async def get_workforce_skill_matrix(
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
):
    """Generates company-wide skill competency matrix across employees, levels, and verified badges."""
    employees = await employee_repo.list_all()
    tracker = SkillProgressionTracker(employee_repo=employee_repo)
    data = tracker.generate_matrix(employees)
    return SkillMatrixResponse(
        total_employees=data["total_employees"],
        skills_catalog=data["skills_catalog"],
        matrix=data["matrix"],
        coverage=data["coverage"],
    )


@router.get("/employees/{employee_id}/progression", response_model=EmployeeProgressionResponse)
async def get_employee_progression(
    employee_id: str,
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
):
    """Retrieves progression history, certified levels, and evidence for an employee."""
    try:
        data = await employee_repo.get_employee_progression(employee_id)
        return EmployeeProgressionResponse(**data)
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
