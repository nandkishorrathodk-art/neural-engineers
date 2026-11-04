import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import get_db_session
from neural.api.main import create_app
from neural.constants import EmployeeStatus
from neural.db.repositories import EmployeeRepository
from neural.employees.factory import EmployeeFactory
from neural.workstations import (
    CANONICAL_WORKSTATIONS,
    VALID_DESK_IDS,
    WorkstationAssignmentService,
    infer_desk_role,
)
from sqlalchemy.ext.asyncio import AsyncSession


def test_canonical_workstation_layout():
    assert len(CANONICAL_WORKSTATIONS) == 11
    assert "WS-BOSS" in VALID_DESK_IDS
    assert "WS-ENG-01" in VALID_DESK_IDS
    assert "WS-OPS-01" in VALID_DESK_IDS
    assert "WS-QA-01" in VALID_DESK_IDS
    assert "WS-RES-01" in VALID_DESK_IDS
    assert "WS-CRE-01" in VALID_DESK_IDS

    service = WorkstationAssignmentService()
    canonical = service.list_canonical_workstations()
    assert len(canonical) == 11
    assert service.is_valid_desk_id("WS-BOSS") is True
    assert service.is_valid_desk_id("WS-INVALID-99") is False


def test_infer_desk_role():
    assert infer_desk_role("Executive Management", "Chief Executive Officer") == "boss"
    assert infer_desk_role("Leadership", "Boss AI") == "boss"
    assert infer_desk_role("Research", "ML Research Scientist") == "research"
    assert infer_desk_role("AI Labs", "Neural Architect") == "research"
    assert infer_desk_role("Design", "UI/UX Designer") == "creative"
    assert infer_desk_role("Creative Studio", "Visual Artist") == "creative"
    assert infer_desk_role("Reliability", "QA Automation Engineer") == "qa"
    assert infer_desk_role("Testing", "Quality Assurance Analyst") == "qa"
    assert infer_desk_role("Infrastructure", "DevOps Engineer") == "devops"
    assert infer_desk_role("Operations", "SRE") == "devops"
    assert infer_desk_role("Frontend Team", "React UI Developer") == "frontend"
    assert infer_desk_role("Engineering", "Software Engineer") == "swe"


def test_allocate_desk_role_priorities():
    service = WorkstationAssignmentService()

    # Boss goes to WS-BOSS
    assert service.allocate_desk("boss", set()) == "WS-BOSS"

    # QA goes to WS-QA-01
    assert service.allocate_desk("qa", set()) == "WS-QA-01"

    # DevOps goes to WS-OPS-01
    assert service.allocate_desk("devops", set()) == "WS-OPS-01"

    # Research goes to WS-RES-01
    assert service.allocate_desk("research", set()) == "WS-RES-01"

    # Creative goes to WS-CRE-01
    assert service.allocate_desk("creative", set()) == "WS-CRE-01"

    # SWE goes to WS-ENG-01
    assert service.allocate_desk("swe", set()) == "WS-ENG-01"


def test_allocate_desk_secondary_department_desks():
    service = WorkstationAssignmentService()

    # When WS-RES-01 is occupied, second researcher gets WS-RES-02
    assert service.allocate_desk("research", {"WS-RES-01"}) == "WS-RES-02"

    # When WS-CRE-01 is occupied, second designer gets WS-CRE-02
    assert service.allocate_desk("creative", {"WS-CRE-01"}) == "WS-CRE-02"

    # When WS-ENG-01 is occupied, engineer gets WS-ENG-02
    assert service.allocate_desk("swe", {"WS-ENG-01"}) == "WS-ENG-02"


def test_allocate_desk_department_fallback():
    service = WorkstationAssignmentService()

    # When both research desks are full, researcher falls back to vacant engineering desk
    occupied = {"WS-RES-01", "WS-RES-02"}
    desk = service.allocate_desk("research", occupied)
    assert desk.startswith("WS-ENG-")

    # When all 11 canonical desks are occupied, allocates deterministic overflow desk
    all_canonical = set(VALID_DESK_IDS)
    overflow_1 = service.allocate_desk("swe", all_canonical)
    assert overflow_1 == "WS-OVERFLOW-01"

    all_with_overflow1 = all_canonical | {"WS-OVERFLOW-01"}
    overflow_2 = service.allocate_desk("qa", all_with_overflow1)
    assert overflow_2 == "WS-OVERFLOW-02"


@pytest.mark.asyncio
async def test_employee_repository_desk_lifecycle(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)

    emp1 = await repo.create(
        employee_id="ANC-EMP-DESK-01",
        name="Desk User One",
        title="Software Engineer",
        system_prompt="Test",
        status=EmployeeStatus.IDLE,
    )
    emp2 = await repo.create(
        employee_id="ANC-EMP-DESK-02",
        name="Desk User Two",
        title="Quality Engineer",
        system_prompt="Test",
        status=EmployeeStatus.IDLE,
    )

    # Assign desk to emp1
    updated1 = await repo.assign_desk(emp1.employee_id, "WS-ENG-01")
    assert updated1.desk_id == "WS-ENG-01"

    # Query by desk_id
    retrieved = await repo.get_by_desk_id("WS-ENG-01")
    assert retrieved is not None
    assert retrieved.employee_id == emp1.employee_id

    # List occupied desks
    occupied = await repo.list_occupied_desks()
    assert "WS-ENG-01" in occupied

    # Attempting to assign WS-ENG-01 to emp2 should raise ValueError (already assigned to)
    with pytest.raises(ValueError, match="already assigned to"):
        await repo.assign_desk(emp2.employee_id, "WS-ENG-01")

    # Release desk from emp1
    released1 = await repo.release_desk(emp1.employee_id)
    assert released1.desk_id is None

    # Now emp2 can take WS-ENG-01
    updated2 = await repo.assign_desk(emp2.employee_id, "WS-ENG-01")
    assert updated2.desk_id == "WS-ENG-01"


@pytest.mark.asyncio
async def test_employee_factory_auto_allocates_persisted_desk(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    # Hire boss
    boss = await factory.hire_employee(
        name="Executive Commander",
        title="CEO",
        department="Executive",
        role_id="boss-executive",
    )
    assert boss.desk_id == "WS-BOSS"

    # Hire QA
    qa = await factory.hire_employee(
        name="Test Lead",
        title="QA Engineer",
        department="Quality Assurance",
        role_id="qa-engineer",
    )
    assert qa.desk_id == "WS-QA-01"

    # Hire SWE with explicit desk_id
    swe = await factory.hire_employee(
        name="Core Hacker",
        title="Backend Engineer",
        department="Engineering",
        desk_id="WS-ENG-03",
    )
    assert swe.desk_id == "WS-ENG-03"

    # Hiring another employee requesting WS-ENG-03 should fail with ValueError
    with pytest.raises(ValueError, match="already assigned to"):
        await factory.hire_employee(
            name="Conflicting Dev",
            title="Backend Engineer",
            department="Engineering",
            desk_id="WS-ENG-03",
        )


@pytest.mark.asyncio
async def test_workstation_assignment_api_routes(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    dev = await factory.hire_employee(
        name="API Dev",
        title="Platform Engineer",
        department="Engineering",
        desk_id="WS-ENG-01",
    )

    app = create_app()

    async def override_get_db_session():
        yield async_session

    app.dependency_overrides[get_db_session] = override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # GET assignments
        res = await client.get("/employees/workstations/assignments")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 11
        occupied = [w for w in data if w["occupant_id"] == dev.employee_id]
        assert len(occupied) == 1
        assert occupied[0]["id"] == "WS-ENG-01"

        # Reassign desk via PUT /employees/{employee_id}/desk
        put_res = await client.put(
            f"/employees/{dev.employee_id}/desk",
            json={"desk_id": "WS-ENG-04"},
        )
        assert put_res.status_code == 200
        put_data = put_res.json()
        assert put_data["desk_id"] == "WS-ENG-04"

        # Release desk via DELETE /employees/{employee_id}/desk
        del_res = await client.delete(f"/employees/{dev.employee_id}/desk")
        assert del_res.status_code == 200
        del_data = del_res.json()
        assert del_data["desk_id"] is None
