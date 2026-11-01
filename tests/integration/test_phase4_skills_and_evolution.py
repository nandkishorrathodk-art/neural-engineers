import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.db.repositories import EmployeeRepository
from neural.employees.factory import EmployeeFactory
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_skills_and_evolution_api_full_lifecycle(async_session: AsyncSession):
    app = create_app()

    async def override_db():
        yield async_session

    app.dependency_overrides[get_db_session] = override_db

    # Bootstrap workforce
    emp_repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(emp_repo)
    emp = await factory.hire_employee(
        name="Morgan Freeman",
        title="Full Stack Developer",
        initial_skills=["Python", "FastAPI"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # ====================================================================
        # 1. Skills Catalog & Benchmarks Routes
        # ====================================================================
        res_cat = await client.get("/skills/catalog")
        assert res_cat.status_code == 200
        catalog = res_cat.json()
        assert len(catalog) >= 5
        assert any(s["name"] == "Python" for s in catalog)
        assert any(s["name"] == "FastAPI" for s in catalog)

        # Single catalog skill
        res_py = await client.get("/skills/catalog/Python")
        assert res_py.status_code == 200
        py_data = res_py.json()
        assert py_data["category"] == "Backend"
        assert "novice" in py_data["available_levels"]

        # Benchmarks list
        res_b = await client.get("/skills/benchmarks?skill_name=Python")
        assert res_b.status_code == 200
        benchmarks = res_b.json()
        assert len(benchmarks) >= 1
        bench_id = benchmarks[0]["benchmark_id"]

        # Single benchmark detail
        res_b_det = await client.get(f"/skills/benchmarks/{bench_id}")
        assert res_b_det.status_code == 200
        assert res_b_det.json()["benchmark_id"] == bench_id

        # ====================================================================
        # 2. Empirical Certification Route
        # ====================================================================
        solution_code = """
def solve(items):
    return [x**2 for x in items if x % 2 == 0]
"""
        cert_payload = {
            "employee_id": emp.employee_id,
            "skill_name": "Python",
            "target_level": "novice",
            "code_submission": solution_code,
            "timeout": 5.0,
        }
        res_cert = await client.post("/skills/certify", json=cert_payload)
        assert res_cert.status_code == 200
        cert_data = res_cert.json()
        assert cert_data["passed"] is True
        assert cert_data["certificate_id"] is not None
        assert cert_data["certificate_hash"] is not None
        assert len(cert_data["certificate_hash"]) == 64
        cert_id = cert_data["certificate_id"]

        # ====================================================================
        # 3. Certificates Query & Verification Routes
        # ====================================================================
        res_list_cert = await client.get(f"/skills/certificates?employee_id={emp.employee_id}")
        assert res_list_cert.status_code == 200
        certs = res_list_cert.json()
        assert len(certs) == 1
        assert certs[0]["certificate_id"] == cert_id

        res_single_cert = await client.get(f"/skills/certificates/{cert_id}")
        assert res_single_cert.status_code == 200
        assert res_single_cert.json()["certificate_id"] == cert_id

        # Cryptographic verification
        res_verify = await client.get(f"/skills/certificates/{cert_id}/verify")
        assert res_verify.status_code == 200
        verify_data = res_verify.json()
        assert verify_data["is_valid"] is True
        assert "verified" in verify_data["message"].lower()

        # ====================================================================
        # 4. Matrix & Employee Progression Routes
        # ====================================================================
        res_matrix = await client.get("/skills/matrix")
        assert res_matrix.status_code == 200
        matrix_data = res_matrix.json()
        assert matrix_data["total_employees"] >= 1
        emp_matrix_row = next(r for r in matrix_data["matrix"] if r["employee_id"] == emp.employee_id)
        assert emp_matrix_row["skills"]["Python"]["verified"] is True
        assert emp_matrix_row["skills"]["Python"]["level"] == "novice"

        res_prog = await client.get(f"/skills/employees/{emp.employee_id}/progression")
        assert res_prog.status_code == 200
        prog_data = res_prog.json()
        assert prog_data["employee_id"] == emp.employee_id
        py_prog = next(s for s in prog_data["skills"] if s["skill_name"] == "Python")
        assert py_prog["verified"] is True
        assert cert_id in py_prog["evidence_task_ids"]

        # ====================================================================
        # 5. Evolution Engine Routes
        # ====================================================================
        res_evo_status = await client.get("/evolution/status")
        assert res_evo_status.status_code == 200
        assert res_evo_status.json()["status"] == "active"

        # Gap analysis
        res_gap = await client.post("/evolution/gap-analysis", json={"include_projects": True, "include_failures": True})
        assert res_gap.status_code == 200
        gap_report = res_gap.json()
        assert "report_id" in gap_report
        assert "gaps" in gap_report

        # Curriculum generation
        res_curr = await client.post("/evolution/curriculum/generate", json={
            "skill_name": "FastAPI",
            "target_level": "competent",
            "gap_reason": "Automated training request",
        })
        assert res_curr.status_code == 200
        curr_data = res_curr.json()
        assert curr_data["skill_name"] == "FastAPI"
        assert len(curr_data["modules"]) >= 1

        # Trigger evolution cycle
        res_cycle = await client.post("/evolution/cycle", json={
            "trigger_reason": "integration_test_trigger",
        })
        assert res_cycle.status_code == 200
        cycle_data = res_cycle.json()
        assert cycle_data["status"] == "completed"
        assert len(cycle_data["persisted_record_ids"]) >= 1

        # List evolution records
        res_records = await client.get("/evolution/records")
        assert res_records.status_code == 200
        records = res_records.json()
        assert len(records) >= 1
        record_id = records[0]["id"]

        # Single record detail
        res_rec_det = await client.get(f"/evolution/records/{record_id}")
        assert res_rec_det.status_code == 200
        assert res_rec_det.json()["id"] == record_id

        # ====================================================================
        # 6. API Error Edge Cases
        # ====================================================================
        # Empty code submission
        res_empty = await client.post("/skills/certify", json={
            "employee_id": emp.employee_id,
            "skill_name": "Python",
            "target_level": "novice",
            "code_submission": "   ",
        })
        assert res_empty.status_code == 400
        assert "empty" in res_empty.json()["detail"].lower()

        # Non-existent employee
        res_no_emp = await client.post("/skills/certify", json={
            "employee_id": "ANC-EMP-NONEXISTENT",
            "skill_name": "Python",
            "target_level": "novice",
            "code_submission": "def solve(x): return x",
        })
        assert res_no_emp.status_code == 404

        # Non-existent certificate
        res_no_cert = await client.get("/skills/certificates/CERT-FAKE-ID")
        assert res_no_cert.status_code == 404

        # Non-existent evolution record
        res_no_rec = await client.get("/evolution/records/REC-FAKE-ID")
        assert res_no_rec.status_code == 404

