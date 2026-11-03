from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from neural.api.deps import (
    get_container_manager,
    get_container_repo,
    get_db_session,
    get_employee_repo,
    require_owner_identity,
)
from neural.api.main import create_app
from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop
from neural.gui_intelligence.benchmarks.osworld_runner import OSWorldRunner
from neural.gui_intelligence.brain.cognitive_controller import DualBrainCognitiveController
from neural.gui_intelligence.models import GUIActionRequest
from neural.gui_intelligence.perception.screen_capture import ScreenCaptureEngine
from neural.gui_intelligence.skills.experience_recorder import ExperienceRecorder
from neural.gui_intelligence.skills.skill_compiler import GUISkillCompiler
from neural.gui_intelligence.skills.skill_registry import get_gui_skill_registry
from neural.gui_intelligence.skills.skill_validator import SkillValidator
from neural.security.kill_switch import reset_kill_switch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def reset_system_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_phase7_gui_intelligence_api_endpoints(async_session: AsyncSession):
    """
    Validates Phase 7 GUI Intelligence API endpoints:
    - POST /gui/plan
    - POST /gui/action
    - POST /gui/chunk
    - GET /gui/screen/capture
    - POST /gui/benchmark/human-speed
    - POST /gui/benchmark/osworld
    - GET /gui/skills
    - POST /gui/skills/compile
    - POST /gui/skills/{id}/certify
    """
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: async_session

    class FakeContainerManager:
        async def get_desktop_by_employee_id(self, employee_id, **kwargs):
            assert employee_id == "ANC-EMP-GUI-API"
            return {"docker_id": "ancos-desktop-anc-emp-gui-api", "status": "running"}

        async def capture_screenshot(self, docker_id):
            assert docker_id == "ancos-desktop-anc-emp-gui-api"
            from io import BytesIO

            import numpy as np
            from PIL import Image

            buffer = BytesIO()
            Image.fromarray(np.zeros((800, 1280, 3), dtype=np.uint8), mode="RGB").save(buffer, format="PNG")
            return buffer.getvalue()

    app.dependency_overrides[require_owner_identity] = lambda: "test-owner"
    app.dependency_overrides[get_container_manager] = lambda: FakeContainerManager()
    app.dependency_overrides[get_container_repo] = lambda: object()
    app.dependency_overrides[get_employee_repo] = lambda: object()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Screen Capture endpoint
        cap_res = await client.get("/gui/screen/capture?employee_id=ANC-EMP-GUI-API")
        assert cap_res.status_code == 200
        cap_data = cap_res.json()
        assert cap_data["resolution"] == [1280, 800]
        assert len(cap_data["frame_hash"]) == 64

        # 2. Planning endpoint (System 2 Deep Brain)
        plan_res = await client.post("/gui/plan", json={"task_objective": "Export report as PDF"})
        assert plan_res.status_code == 200
        plan_data = plan_res.json()
        assert len(plan_data["steps"]) >= 2
        assert plan_data["task_objective"] == "Export report as PDF"

        # 3. Single Action dispatch
        act_res = await client.post(
            "/gui/action",
            json={
                "action_id": "act_api_1",
                "employee_id": "emp_gui_api",
                "action_type": "MOUSE_MOVE",
                "coordinates": [500, 300],
                "confidence": 0.98,
                "risk_level": "LOW",
            },
        )
        assert act_res.status_code == 200
        act_data = act_res.json()
        assert act_data["status"] == "SUCCESS"
        assert len(act_data["signature"]) == 64

        # 4. Action Chunk dispatch
        chunk_res = await client.post(
            "/gui/chunk",
            json=[
                {
                    "action_id": "chunk_act_1",
                    "employee_id": "emp_gui_api",
                    "action_type": "MOUSE_MOVE",
                    "coordinates": [200, 200],
                    "confidence": 0.99,
                    "risk_level": "LOW",
                },
                {
                    "action_id": "chunk_act_2",
                    "employee_id": "emp_gui_api",
                    "action_type": "WAIT_SETTLE",
                    "confidence": 1.0,
                    "risk_level": "LOW",
                },
            ],
        )
        assert chunk_res.status_code == 200
        chunk_data = chunk_res.json()
        assert len(chunk_data) == 2
        assert chunk_data[0]["status"] == "SUCCESS"

        # 5. HumanSpeed 4x Benchmark endpoint
        speed_res = await client.post(
            "/gui/benchmark/human-speed",
            json={
                "task_id": "speed_task_1",
                "task_name": "Spreadsheet Automation",
                "human_time_seconds": 40.0,
                "ancos_time_seconds": 8.0,
                "passed_verification": True,
            },
        )
        assert speed_res.status_code == 200
        speed_data = speed_res.json()
        assert speed_data["speedup_ratio"] == 5.0
        assert speed_data["meets_4x_target"] is True
        assert speed_data["disqualified"] is False

        # 6. OSWorld Benchmark endpoint
        osworld_res = await client.post(
            "/gui/benchmark/osworld",
            json={
                "task_id": "osworld_1",
                "task_name": "Daily Form Processing",
                "task_objective": "Fill out employee registration form",
                "category": "daily_workflows",
            },
        )
        assert osworld_res.status_code == 200
        osworld_data = osworld_res.json()
        assert osworld_data["task_id"] == "osworld_1"
        assert osworld_data["passed"] is True

        # 7. Skills list (initially empty or default)
        skills_res = await client.get("/gui/skills")
        assert skills_res.status_code == 200

        # 8. Compile Skill endpoint
        rec = ExperienceRecorder()
        rec.start_trajectory("task_int_1", "emp_test", "AppX")
        frame = ScreenCaptureEngine().capture_frame()
        loop = GUIExecutionLoop()
        act_req = GUIActionRequest(
            action_id="act_val_int",
            employee_id="emp_test",
            action_type="MOUSE_CLICK",
            coordinates=(210, 160),
            confidence=0.98,
        )
        rec.record_step(frame, act_req, loop.execute_action(act_req))
        traj = rec.finalize_trajectory(verification_passed=True, verified_speedup_ratio=4.2)
        comp_res = await client.post(
            "/gui/skills/compile",
            json={
                "trajectory": traj.model_dump(),
                "skill_name": "AppX_Routine",
                "description": "Certified AppX routine",
            },
        )
        assert comp_res.status_code == 200
        comp_data = comp_res.json()
        skill_id = comp_data["skill_id"]
        assert comp_data["skill_name"] == "AppX_Routine"

        # 9. Certify Skill endpoint (3-permutation held-out testing)
        cert_res = await client.post(f"/gui/skills/{skill_id}/certify")
        assert cert_res.status_code == 200
        cert_data = cert_res.json()
        assert cert_data["passed"] is True
        assert cert_data["consecutive_passes"] == 3
        assert cert_data["certificate_hash"] is not None


@pytest.mark.asyncio
async def test_end_to_end_dual_brain_and_skill_certification_lifecycle():
    """
    End-to-end integration test proving the complete Phase 7 lifecycle:
    1. Employee perceives synthetic desktop via ScreenCaptureEngine
    2. DualBrainCognitiveController plans with System 2 and executes with System 1
    3. ExperienceRecorder logs timestamped trajectory and hashes
    4. GUISkillCompiler extracts relative visual anchors and compiles skill
    5. SkillValidator runs 3 held-out layout/theme permutations
    6. GUISkillRegistry certifies skill upon 100% pass rate
    7. OSWorldRunner executes benchmark protocol achieving verified receipts
    """
    capture = ScreenCaptureEngine()
    recorder = ExperienceRecorder()
    controller = DualBrainCognitiveController(capture_engine=capture, experience_recorder=recorder)
    compiler = GUISkillCompiler()
    registry = get_gui_skill_registry()
    validator = SkillValidator(registry=registry, capture_engine=capture)
    osworld = OSWorldRunner(controller=controller, capture_engine=capture)

    # 1. Execute task with dual brain
    traj_id = recorder.start_trajectory("e2e_task_1", "emp_senior_qa", "OfficeCalc")
    exec_res = await controller.execute_task("Save document and export")
    assert exec_res["success"]
    assert exec_res["deep_brain_calls"] >= 1
    assert exec_res["fast_brain_calls"] >= 1

    # 2. Finalize trajectory
    traj = recorder.finalize_trajectory(verification_passed=True, verified_speedup_ratio=4.5)
    assert traj.trajectory_id == traj_id

    # 3. Compile skill
    skill = compiler.compile_trajectory(traj, "OfficeCalc_SaveExport")
    registry.register_skill(skill)
    assert not registry.is_certified(skill.skill_id)

    # 4. Validate skill across 3 held-out permutations
    val_res = validator.validate_skill(skill)
    assert val_res.passed
    assert val_res.consecutive_passes == 3
    assert registry.is_certified(skill.skill_id)

    # 5. Run OSWorld benchmark runner
    osworld_res = await osworld.run_task(
        task_id="osworld_e2e_1",
        task_name="Spreadsheet File Save",
        task_objective="Save file and verify",
    )
    assert osworld_res.passed
    assert osworld_res.score == 1.0
