from __future__ import annotations

import pytest
from neural.db.models import Employee
from neural.db.repositories import EmployeeRepository
from neural.employees.factory import EmployeeFactory, generate_default_avatar_config
from neural.workstations.layout import CANONICAL_WORKSTATIONS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def test_deterministic_avatar_generation():
    # Same seed and role should produce 100% identical config
    cfg1 = generate_default_avatar_config("swe", "ANC-EMP-001Alice", title="Senior Engineer", department="Engineering")
    cfg2 = generate_default_avatar_config("swe", "ANC-EMP-001Alice", title="Senior Engineer", department="Engineering")
    assert cfg1 == cfg2
    assert "hairColor" in cfg1
    assert "suitColor" in cfg1
    assert "skinTone" in cfg1
    assert cfg1["hairStyle"] == "spiky"
    assert cfg1["hasHeadphones"] is True

    # Boss role has distinct theme
    cfg_boss = generate_default_avatar_config("boss", "ANC-EMP-BOSS", title="Boss AI", department="Executive")
    assert cfg_boss["hasVisor"] is True
    assert cfg_boss["hairColor"] == "#4d5c70"

    # QA role has distinct theme
    cfg_qa = generate_default_avatar_config("qa", "ANC-EMP-QA01", title="QA Engineer", department="Quality & Reliability")
    assert cfg_qa["hasVisor"] is True
    assert cfg_qa["hairColor"] == "#544134"


@pytest.mark.asyncio
async def test_avatar_config_persistence_in_database(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    # Recruit employee without explicit avatar (auto-generates)
    emp1 = await factory.hire_employee(
        name="Bob Martinez",
        title="Full Stack Engineer",
        department="Engineering",
        desk_id="WS-ENG-01",
    )
    assert emp1.avatar_config is not None
    assert "suitColor" in emp1.avatar_config
    assert "skinTone" in emp1.avatar_config

    # Query directly from DB
    res = await async_session.execute(select(Employee).where(Employee.employee_id == emp1.employee_id))
    fetched = res.scalar_one()
    assert fetched.avatar_config == emp1.avatar_config
    assert fetched.desk_id == "WS-ENG-01"

    # Browser refresh simulation: re-query preserves exact appearance
    refreshed = await repo.get_by_employee_id(emp1.employee_id)
    assert refreshed is not None
    assert refreshed.avatar_config == emp1.avatar_config


def test_workstation_layout_canonical_mapping():
    """
    Verifies that backend CANONICAL_WORKSTATIONS has all expected desk IDs
    matching the frontend OfficeLayout.ts specification.
    """
    desk_ids = {ws["id"] for ws in CANONICAL_WORKSTATIONS}
    expected_desks = {
        "WS-BOSS",
        "WS-ENG-01",
        "WS-ENG-02",
        "WS-ENG-03",
        "WS-ENG-04",
        "WS-RES-01",
        "WS-RES-02",
        "WS-CRE-01",
        "WS-CRE-02",
        "WS-OPS-01",
        "WS-QA-01",
    }
    for expected in expected_desks:
        assert expected in desk_ids, f"Expected desk {expected} missing from CANONICAL_WORKSTATIONS"


@pytest.mark.asyncio
async def test_explicit_custom_avatar_config(async_session: AsyncSession):
    repo = EmployeeRepository(async_session)
    factory = EmployeeFactory(repo)

    custom_avatar = {
        "hairColor": "#ff00ff",
        "suitColor": "#00ffff",
        "accentColor": "#ffff00",
        "skinTone": "#e8b995",
        "hairStyle": "cyber",
        "hasVisor": True,
        "hasHeadphones": True,
        "hasGlasses": True,
    }

    emp = await factory.hire_employee(
        name="Cyber Specialist",
        title="Security Analyst",
        department="Engineering",
        desk_id="WS-ENG-02",
        avatar_config=custom_avatar,
    )
    assert emp.avatar_config == custom_avatar

    fetched = await repo.get_by_employee_id(emp.employee_id)
    assert fetched is not None
    assert fetched.avatar_config["hairStyle"] == "cyber"
