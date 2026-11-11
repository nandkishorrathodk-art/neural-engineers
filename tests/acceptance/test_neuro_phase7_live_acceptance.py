"""
ANCOS NEURO-7 Live Acceptance Test Suite.
Empirically verifies Advanced GUI & Computer-Use Intelligence across:
1. Owner Authentication, Budget Setup & Repositories Initialization.
2. Dynamic Workforce Recruitment & Workstation Allocation (ANC-EMP-GUI-QA, WS-QA-01, unverified skills).
3. Strict GUI-Only Mode & Anti-Cheating Invariants (fails closed with StrictGUIContractViolation on DOM/CDP/Playwright/Selenium/CLI bypasses).
4. Calibrated Visual Grounding & Zero Blind Center-Clicking (confidence >= 0.88, aborts without motor movement when ungrounded).
5. Dynamic Transient Popup, Modal Dialog & Navigation Error Recovery (spikes belief uncertainty, engages recovery, dismisses modal, restores settled screen).
6. Plan Step Bounding (max_steps) & Truncation Guard (plan_truncated reported, truncated plan cannot report whole-task success).
7. Multi-Application Desktop Workflow Execution (Document Editor + Spreadsheet Calculator cross-application coordination).
8. Independent Visual Verification & Semantic Postcondition Judgment (bounds post-action screenshot hash, raster OCR, and optical delta to GUITaskCertificate).
9. ANCOS-HumanSpeed 4x Benchmark Comparator (evaluates T_human / T_ancos, enforces 0.0x disqualification rule on unverified or safety-violating runs).
10. Visual Skill Compilation & 3-Permutation Held-Out Generalization Certification (records trajectory, compiles reusable visual anchors, certifies in registry).
11. OSWorld 2.1 Benchmark Protocol Execution (standard daily workflow benchmark with independent receipts).
12. Empirical Skill Certification & Cryptographic Audit Hash-Chain Integrity (skill promoted to verified=True, AuditLogChainVerifier validates immutable audit chain).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from neural.db.models import (
    BudgetAccount,
    Project,
)
from neural.db.repositories import (
    AuditRepository,
    BudgetRepository,
    EmployeeRepository,
    ProjectRepository,
    ResourceLeaseRepository,
    TaskRepository,
)
from neural.employees.factory import EmployeeFactory
from neural.exceptions import (
    StrictGUIContractViolation,
    VisualUncertaintyError,
)
from neural.execution.contracts import CapabilityPolicyEnforcer
from neural.gui_intelligence.action.execution_loop import GUIExecutionLoop
from neural.gui_intelligence.benchmarks.human_speed import HumanSpeedComparator
from neural.gui_intelligence.benchmarks.osworld_runner import OSWorldRunner
from neural.gui_intelligence.brain.cognitive_controller import DualBrainCognitiveController
from neural.gui_intelligence.brain.fast_policy import System1FastPolicy
from neural.gui_intelligence.brain.task_planner import PlanStep, System2TaskPlanner
from neural.gui_intelligence.models import (
    GUIActionRequest,
    VisualElement,
)
from neural.gui_intelligence.perception.screen_capture import ScreenCaptureEngine
from neural.gui_intelligence.perception.visual_grounding import VisualGrounder
from neural.gui_intelligence.recovery.state_recovery import GUIStateRecovery
from neural.gui_intelligence.recovery.visibility_recovery import VisibilityRecoveryEngine
from neural.gui_intelligence.safety.gui_action_policy import GUIActionPolicy
from neural.gui_intelligence.safety.risk_guard import GUIRiskGuard
from neural.gui_intelligence.skills.experience_recorder import ExperienceRecorder
from neural.gui_intelligence.skills.skill_compiler import GUISkillCompiler
from neural.gui_intelligence.skills.skill_registry import get_gui_skill_registry
from neural.gui_intelligence.skills.skill_validator import SkillValidator
from neural.gui_intelligence.verification.task_completion import TaskCompletionCertificateGenerator
from neural.gui_intelligence.verification.visual_verifier import (
    IndependentVisualVerifier,
)
from neural.gui_intelligence.world_model.visual_belief import VisualBeliefStateManager
from neural.gui_intelligence.world_model.window_state import WindowStateTracker
from neural.observability.audit import AuditLogChainVerifier
from neural.security.kill_switch import reset_kill_switch
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def reset_system_safety_state():
    reset_kill_switch()
    yield
    reset_kill_switch()


@pytest.mark.asyncio
async def test_neuro_phase7_live_acceptance_full_scenario(
    async_session: AsyncSession,
    tmp_path,
):
    """
    NEURO-7 Live Acceptance Scenario:
    Demonstrates the complete Advanced GUI & Computer-Use Intelligence lifecycle:
    strict GUI-only anti-cheating mode, calibrated visual grounding, dynamic popup recovery,
    plan step bounding, multi-application workflow execution, independent visual verification,
    HumanSpeed 4x baseline evaluation with disqualification rules, visual skill compilation
    with held-out generalization certification, OSWorld 2.1 benchmark execution, and
    cryptographic audit hash-chain integrity.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # =========================================================================
    # STEP 1: Owner Authentication, Budget Setup & Repositories Initialization
    # =========================================================================
    owner_identity = "owner@ancos.corp"
    assert "@" in owner_identity

    budget_repo = BudgetRepository(async_session)
    month_scope = f"organization-month:{now:%Y-%m}"
    account = BudgetAccount(
        scope_key=month_scope,
        cap_usd=Decimal("2000.00"),
        reserved_usd=Decimal("0.00"),
        spent_usd=Decimal("0.00"),
    )
    async_session.add(account)
    await async_session.commit()

    available_quota = await budget_repo.get_available_quota(month_scope)
    assert available_quota >= Decimal("1500.00"), "Step 1 Failed: Budget quota insufficient"

    employee_repo = EmployeeRepository(async_session)
    task_repo = TaskRepository(async_session)
    project_repo = ProjectRepository(async_session)
    audit_repo = AuditRepository(async_session)
    lease_repo = ResourceLeaseRepository(async_session)
    assert task_repo is not None and project_repo is not None

    # =========================================================================
    # STEP 2: Boss Recruits Workforce & Provisions Workstation Desk
    # =========================================================================
    factory = EmployeeFactory(employee_repo)
    factory.settings.EMPLOYEE_WORKSPACE_BASE = str(tmp_path)

    qa_emp = await factory.recruit_dynamic_employee(
        role_title="Lead GUI Automation & QA Specialist",
        department="Quality & Reliability",
        initial_skills=["GUI Automation", "Computer-Use Intelligence", "Visual Quality Assurance"],
    )
    assert qa_emp.employee_id.startswith("ANC-EMP-"), "Step 2 Failed: Employee ID format"
    assert all(not s.verified for s in qa_emp.skills), "Step 2 Failed: Initial skills must be unverified"
    assert qa_emp.desk_id == "WS-QA-01", f"Step 2 Failed: Expected WS-QA-01, got {qa_emp.desk_id}"

    # Workstation lease reservation
    lease = await lease_repo.claim(
        resource_key=f"desktop:pool:{qa_emp.desk_id}",
        employee_id=qa_emp.employee_id,
        session_id=f"session_gui_{qa_emp.employee_id}",
        lease_seconds=3600,
    )
    assert lease is not None, "Step 2 Failed: Workstation resource lease claim failed"
    assert lease["status"] == "active", "Step 2 Failed: Workstation lease is not active"

    project = Project(
        id="PROJ-NEURO-7-ACCEPTANCE",
        name="Advanced GUI Intelligence Enterprise Acceptance",
        objective="Empirically verify multi-application GUI computer-use with anti-cheating invariants.",
        status="active",
        execution_plan={},
    )
    async_session.add(project)
    await async_session.flush()

    await audit_repo.create_entry(
        actor="BossAI",
        action="recruit_and_lease",
        resource_type="employee",
        resource_id=qa_emp.employee_id,
        details={
            "employee_id": qa_emp.employee_id,
            "desk_id": qa_emp.desk_id,
            "lease_id": lease["lease_id"],
        },
    )

    # =========================================================================
    # STEP 3: Strict GUI-Only Mode & Anti-Cheating Invariants
    # =========================================================================
    enforcer = CapabilityPolicyEnforcer()
    assert enforcer is not None
    gui_policy = GUIActionPolicy()
    risk_guard = GUIRiskGuard(policy=gui_policy)
    exec_loop = GUIExecutionLoop(risk_guard=risk_guard)

    # Prohibited shortcut vectors that must fail closed with StrictGUIContractViolation
    prohibited_vectors = [
        "document.querySelector('#submit-btn').click()",
        "playwright.page.locator('button').click()",
        "selenium.webdriver.find_element(By.ID, 'save')",
        "cdp.send('Input.dispatchMouseEvent', {type: 'mousePressed'})",
        "at-spi.get_root_tree().find_control('Save')",
        "xdotool key Return",
    ]

    for shortcut in prohibited_vectors:
        # Enforcer contract rejection
        with pytest.raises(StrictGUIContractViolation, match="prohibited shortcut"):
            gui_policy.validate_gui_contract(shortcut)

        # Execution loop intercept & status BLOCKED_BY_SAFETY
        blocked_req = GUIActionRequest(
            action_id=f"act_cheat_{abs(hash(shortcut)) % 10000}",
            employee_id=qa_emp.employee_id,
            action_type="KEY_TYPE",
            text_content=shortcut,
            confidence=0.98,
        )
        receipt = exec_loop.execute_action(blocked_req)
        assert receipt.status == "BLOCKED_BY_SAFETY", f"Step 3 Failed: Shortcut {shortcut} was not blocked"
        assert "Strict GUI-Only Contract" in (receipt.error_message or "")

    await audit_repo.create_entry(
        actor=qa_emp.employee_id,
        action="enforce_gui_contract",
        resource_type="gui_contract",
        resource_id="StrictGUIOnly",
        details={"prohibited_vectors_blocked": len(prohibited_vectors)},
    )

    # =========================================================================
    # STEP 4: Calibrated Visual Grounding & Zero Blind Center-Clicking
    # =========================================================================
    capture_engine = ScreenCaptureEngine()
    frame_desktop = capture_engine.render_synthetic_desktop()
    grounder = VisualGrounder()

    # Confident grounding: target "Save" on rendered synthetic desktop
    target_save = grounder.ground_query(frame_desktop, "Save", expected_element_type="button")
    assert target_save.is_confident, "Step 4 Failed: Expected confident target for 'Save'"
    assert target_save.confidence >= 0.88, "Step 4 Failed: Confidence below 0.88 threshold"
    assert target_save.variance <= 0.12, "Step 4 Failed: Variance above 0.12 threshold"
    assert target_save.coordinates == (145, 176), f"Step 4 Failed: Unexpected Save coordinates: {target_save.coordinates}"

    # Non-existent query raises VisualUncertaintyError when required
    with pytest.raises(VisualUncertaintyError):
        grounder.ground_query(frame_desktop, "NonExistentWidgetXYZ", require_confidence=True)

    # Invariant: Low confidence actions cannot be dispatched to screen
    low_conf_req = GUIActionRequest(
        action_id="act_low_conf",
        employee_id=qa_emp.employee_id,
        action_type="MOUSE_CLICK",
        coordinates=(640, 400),
        confidence=0.45,  # Below 0.88 threshold
    )
    low_conf_receipt = exec_loop.execute_action(low_conf_req)
    assert low_conf_receipt.status == "UNCERTAINTY_ESCALATION", "Step 4 Failed: Low confidence action must escalate"

    # Controller never blindly center-clicks on uncertain grounding
    class MockUncertainFastPolicy(System1FastPolicy):
        def select_action_chunk(self, step, frame, belief_state=None):
            raise VisualUncertaintyError("Target ungroundable in screen pixels")

    uncertain_controller = DualBrainCognitiveController(
        fast_policy=MockUncertainFastPolicy(),
        capture_engine=capture_engine,
    )
    uncertain_result = await uncertain_controller.execute_task("Click missing element", max_steps=1)
    assert uncertain_result["success"] is False, "Step 4 Failed: Uncertain task must not succeed"
    assert uncertain_result["completed_steps"] == 0, "Step 4 Failed: Uncertain step must not be marked completed"
    assert uncertain_result["total_actions_dispatched"] == 0, "Step 4 Failed: Dispatched blind action on uncertainty"

    await audit_repo.create_entry(
        actor=qa_emp.employee_id,
        action="calibrate_grounding",
        resource_type="visual_grounder",
        resource_id="ANCOS-Grounder",
        details={"calibrated_threshold": 0.88, "zero_blind_clicks_enforced": True},
    )

    # =========================================================================
    # STEP 5: Dynamic Transient Popup, Modal Dialog & Navigation Error Recovery
    # =========================================================================
    window_tracker = WindowStateTracker()
    belief_mgr = VisualBeliefStateManager()
    visibility_recovery = VisibilityRecoveryEngine()
    assert visibility_recovery is not None
    state_recovery = GUIStateRecovery()

    # Desktop with active modal popup alert
    frame_with_dialog = capture_engine.render_multi_app_desktop(active_app="editor", include_dialog=True)
    elements_dialog = capture_engine.get_last_rendered_elements()
    vis_elements = [
        VisualElement(
            element_id=f"dlg_elem_{i}",
            bounding_box=el["rect"],
            element_type=el.get("type", "button"),
            visual_text=el.get("text"),
            visual_signature="sig",
            confidence=0.98,
            is_interactive=True,
            is_focused=el.get("is_focused", False),
        )
        for i, el in enumerate(elements_dialog)
    ]

    vis_windows = window_tracker.update_from_elements(frame_with_dialog, vis_elements)
    active_dialogs = window_tracker.detect_active_dialogs()
    assert len(active_dialogs) >= 1, "Step 5 Failed: Active modal dialog not detected"

    # Uncertainty spikes when popup is present
    belief_state = belief_mgr.update_belief(
        frame=frame_with_dialog,
        visible_windows=vis_windows,
        active_dialogs=active_dialogs,
    )
    assert belief_state.uncertainty_score >= 0.45, "Step 5 Failed: Belief uncertainty must spike with popup"
    assert belief_mgr.requires_escalation is True, "Step 5 Failed: Belief manager must require escalation"

    # Recovery: Dismiss modal dialog via Escape hotkey
    state_recovery.dismiss_modal()
    capture_engine.apply_visual_action("KEY_HOTKEY", None, text="Escape")

    # Post-recovery capture: Dialog is cleared
    frame_recovered = capture_engine.capture_frame()
    rendered_post = capture_engine.get_last_rendered_elements()
    post_dialogs = [el for el in rendered_post if el.get("type") == "dialog"]
    assert len(post_dialogs) == 0, "Step 5 Failed: Modal dialog should be dismissed after recovery"

    post_belief = belief_mgr.update_belief(
        frame=frame_recovered,
        visible_windows=window_tracker.update_from_elements(frame_recovered, []),
        active_dialogs=[],
    )
    assert post_belief.uncertainty_score < 0.12, "Step 5 Failed: Uncertainty must settle post-recovery"

    await audit_repo.create_entry(
        actor=qa_emp.employee_id,
        action="dismiss_modal_dialog",
        resource_type="window_manager",
        resource_id="dialog_alert",
        details={"dialog_dismissed": True, "settled_uncertainty": post_belief.uncertainty_score},
    )

    # =========================================================================
    # STEP 6: Plan Step Bounding (max_steps) & Truncation Guard
    # =========================================================================
    # Task with 3 planned steps bounded by max_steps=2
    bounded_controller = DualBrainCognitiveController(capture_engine=capture_engine)
    trunc_result = await bounded_controller.execute_task("Save document and export", max_steps=2)

    assert trunc_result["plan_truncated"] is True, "Step 6 Failed: Must report plan_truncated=True"
    assert trunc_result["steps_attempted"] == 2, "Step 6 Failed: Must bound steps to max_steps"
    assert trunc_result["total_steps_in_plan"] == 3, "Step 6 Failed: Plan has 3 total steps"
    assert trunc_result["success"] is False, "Step 6 Failed: Truncated plan cannot report success"

    await audit_repo.create_entry(
        actor=qa_emp.employee_id,
        action="enforce_max_steps",
        resource_type="task_plan",
        resource_id="plan_truncated_test",
        details={"max_steps": 2, "plan_truncated": True, "success": False},
    )

    # =========================================================================
    # STEP 7: Multi-Application Desktop Workflow Execution
    # =========================================================================
    # Multi-app desktop: Document Editor + Spreadsheet Calculator
    capture_engine.render_multi_app_desktop(active_app="editor", include_dialog=False)

    planner = System2TaskPlanner()
    # Explicit custom multi-app execution plan
    def multi_app_plan_generator(task_obj, belief):
        return [
            PlanStep(
                step_id="step_app1_type",
                phase_name="Enter Audit Results",
                action_intent="TYPE",
                target_descriptor="text_input[label='Report Body']",
                input_value="Annual Performance Audit Results",
                expected_postcondition="Report text entered and verified",
            ),
            PlanStep(
                step_id="step_app1_save",
                phase_name="Save Document",
                action_intent="CLICK",
                target_descriptor="button[text='Save']",
                expected_postcondition="File saved",
            ),
            PlanStep(
                step_id="step_app2_calc",
                phase_name="Compute Spreadsheet Analytics",
                action_intent="CLICK",
                target_descriptor="button[text='Calculate']",
                expected_postcondition="Result: 42",
            ),
        ]

    planner.set_custom_plan_generator(multi_app_plan_generator)
    recorder = ExperienceRecorder()
    multi_controller = DualBrainCognitiveController(
        deep_planner=planner,
        capture_engine=capture_engine,
        experience_recorder=recorder,
    )

    traj_id = recorder.start_trajectory("traj_phase7_acceptance", qa_emp.employee_id, "EnterpriseMultiApp")
    assert traj_id.startswith("traj_")
    exec_result = await multi_controller.execute_task("Multi-app audit report and calculations", max_steps=10)

    assert exec_result["success"] is True, "Step 7 Failed: Multi-app task execution failed"
    assert exec_result["completed_steps"] == 3, "Step 7 Failed: Expected 3 completed steps"
    assert exec_result["plan_truncated"] is False, "Step 7 Failed: Plan should not be truncated"
    assert exec_result["total_actions_dispatched"] >= 3, "Step 7 Failed: Expected >= 3 actions dispatched"

    # =========================================================================
    # STEP 8: Independent Visual Verification & Semantic Postcondition Judgment
    # =========================================================================
    final_frame = capture_engine.capture_frame()
    visual_verifier = IndependentVisualVerifier()

    # 1. Semantic postcondition verification on final screen pixels
    post_verification = visual_verifier.verify_postcondition(
        frame_initial=frame_desktop,
        frame_final=final_frame,
        expected_text="Annual Performance Audit Results",
        expected_postcondition="Result: 42",
        forbidden_text="Critical Error",
        min_delta_magnitude=0.0001,
    )
    assert post_verification.passed is True, f"Step 8 Failed: Postcondition verification failed: {post_verification.details}"
    assert post_verification.score == 1.0, "Step 8 Failed: Score must be 1.0 on passed verification"
    assert post_verification.screenshot_hash == final_frame.frame_hash, "Step 8 Failed: Hash mismatch"
    assert post_verification.delta_magnitude >= 0.0001, "Step 8 Failed: Delta magnitude below threshold"

    # 2. Cryptographic Task Completion Certificate
    cert_gen = TaskCompletionCertificateGenerator()
    cert = cert_gen.generate_certificate(
        task_id="TASK-N7-01-MULTIAPP",
        employee_id=qa_emp.employee_id,
        verification_result=post_verification,
        speedup_ratio=5.0,
        total_actions=exec_result["total_actions_dispatched"],
    )
    assert cert.passed is True, "Step 8 Failed: Certificate passed status"
    assert cert_gen.verify_certificate(cert) is True, "Step 8 Failed: Certificate signature verification failed"

    # Tampering with score or hash invalidates cryptographic signature
    tampered_cert = TaskCompletionCertificateGenerator().generate_certificate(
        task_id="TASK-N7-01-MULTIAPP",
        employee_id=qa_emp.employee_id,
        verification_result=post_verification,
        speedup_ratio=5.0,
    )
    tampered_cert.score = 0.40  # Tampering
    assert cert_gen.verify_certificate(tampered_cert) is False, "Step 8 Failed: Tampered certificate was not rejected"

    await audit_repo.create_entry(
        actor="IndependentVisualVerifier",
        action="issue_certificate",
        resource_type="certificate",
        resource_id=cert.certificate_id,
        details={
            "certificate_id": cert.certificate_id,
            "signature": cert.signature,
            "screenshot_hash": cert.screenshot_hash,
            "delta_magnitude": post_verification.delta_magnitude,
        },
    )

    # =========================================================================
    # STEP 9: ANCOS-HumanSpeed 4x Benchmark Comparator
    # =========================================================================
    # Case A: Valid accelerated run exceeding 4x target
    valid_speedup = HumanSpeedComparator.evaluate_speedup(
        task_id="TASK-N7-01-MULTIAPP",
        task_name="Multi-App Audit and Calculation",
        human_time_seconds=60.0,
        ancos_time_seconds=12.0,
        passed_verification=True,
        safety_violation=False,
    )
    assert valid_speedup.speedup_ratio == 5.0, "Step 9 Failed: Expected 5.0x speedup"
    assert valid_speedup.meets_4x_target is True, "Step 9 Failed: Should meet 4x target"
    assert valid_speedup.disqualified is False, "Step 9 Failed: Legitimate run should not be disqualified"

    # Case B: Disqualification Rule 1 — Failed verification zeroes out speedup
    unverified_speedup = HumanSpeedComparator.evaluate_speedup(
        task_id="TASK-N7-02-FAILED-VERIF",
        task_name="Unverified Fast Execution",
        human_time_seconds=60.0,
        ancos_time_seconds=10.0,
        passed_verification=False,
    )
    assert unverified_speedup.speedup_ratio == 0.0, "Step 9 Failed: Failed verification must force speedup=0.0"
    assert unverified_speedup.meets_4x_target is False, "Step 9 Failed: Failed task cannot meet target"
    assert unverified_speedup.disqualified is True, "Step 9 Failed: Failed verification must disqualify"

    # Case C: Disqualification Rule 2 — Safety violation zeroes out speedup
    safety_speedup = HumanSpeedComparator.evaluate_speedup(
        task_id="TASK-N7-03-SAFETY-VIOLATION",
        task_name="Bypassed Policy Execution",
        human_time_seconds=60.0,
        ancos_time_seconds=8.0,
        passed_verification=True,
        safety_violation=True,
    )
    assert safety_speedup.speedup_ratio == 0.0, "Step 9 Failed: Safety violation must force speedup=0.0"
    assert safety_speedup.disqualified is True, "Step 9 Failed: Safety violation must disqualify"

    await audit_repo.create_entry(
        actor=qa_emp.employee_id,
        action="compare_speedup",
        resource_type="benchmark",
        resource_id="HumanSpeedComparator",
        details={
            "valid_speedup": valid_speedup.speedup_ratio,
            "disqualification_rules_verified": True,
        },
    )

    # =========================================================================
    # STEP 10: Visual Skill Compilation & 3-Permutation Held-Out Certification
    # =========================================================================
    trajectory = recorder.finalize_trajectory(verification_passed=True, verified_speedup_ratio=5.0)
    compiler = GUISkillCompiler()
    skill = compiler.compile_trajectory(
        trajectory=trajectory,
        skill_name="EnterpriseMultiApp_Workflow",
        description="Automated cross-application report and analytics routine",
    )
    assert skill.skill_id.startswith("skill_gui_"), "Step 10 Failed: Skill ID format"
    assert len(skill.actions) >= 2, "Step 10 Failed: Compiled skill missing visual steps"

    registry = get_gui_skill_registry()
    registry.register_skill(skill)
    assert registry.is_certified(skill.skill_id) is False, "Step 10 Failed: Skill must start uncertified"

    # Validate against 3 held-out visual permutations (themes, layouts, styling)
    validator = SkillValidator(registry=registry, capture_engine=capture_engine)
    val_result = validator.validate_skill(skill)

    assert val_result.passed is True, "Step 10 Failed: Skill validation failed"
    assert val_result.consecutive_passes == 3, "Step 10 Failed: Expected 3 consecutive passes"
    assert registry.is_certified(skill.skill_id) is True, "Step 10 Failed: Skill must be certified in registry"
    assert val_result.certificate_hash is not None, "Step 10 Failed: Certificate hash missing"

    await audit_repo.create_entry(
        actor=qa_emp.employee_id,
        action="certify_visual_skill",
        resource_type="gui_skill",
        resource_id=skill.skill_id,
        details={
            "skill_id": skill.skill_id,
            "consecutive_passes": val_result.consecutive_passes,
            "certificate_hash": val_result.certificate_hash,
        },
    )

    # =========================================================================
    # STEP 11: OSWorld 2.1 Benchmark Protocol Execution
    # =========================================================================
    osworld_runner = OSWorldRunner(controller=multi_controller, capture_engine=capture_engine)
    osworld_res = await osworld_runner.run_task(
        task_id="osworld_phase7_acceptance",
        task_name="Daily Multi-App Enterprise Workflow",
        task_objective="Multi-app audit report and calculations",
        category="daily_workflows",
        max_steps=10,
    )
    assert osworld_res.passed is True, f"Step 11 Failed: OSWorld run failed: {osworld_res.error_message}"
    assert osworld_res.score == 1.0, "Step 11 Failed: OSWorld benchmark score must be 1.0"
    assert osworld_res.total_steps >= 2, "Step 11 Failed: OSWorld completed steps"

    await audit_repo.create_entry(
        actor=qa_emp.employee_id,
        action="run_osworld_benchmark",
        resource_type="osworld_task",
        resource_id=osworld_res.task_id,
        details={
            "task_id": osworld_res.task_id,
            "score": osworld_res.score,
            "elapsed_time_seconds": osworld_res.elapsed_time_seconds,
        },
    )

    # =========================================================================
    # STEP 12: Empirical Skill Certification & Cryptographic Audit Hash-Chain Integrity
    # =========================================================================
    # 1. Promote employee's unverified skill "GUI Automation" based on passing evidence
    skill_gui = next((s for s in qa_emp.skills if s.skill_name == "GUI Automation"), None)
    assert skill_gui is not None
    skill_gui.verified = True
    skill_gui.certified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await async_session.commit()

    # Re-read and confirm verified state
    reloaded_emp = await employee_repo.get_by_employee_id(qa_emp.employee_id)
    assert reloaded_emp is not None
    reloaded_skill = next((s for s in reloaded_emp.skills if s.skill_name == "GUI Automation"), None)
    assert reloaded_skill is not None and reloaded_skill.verified is True, "Step 12 Failed: Skill was not verified"

    # 2. Immutable hash-chain verification over all Phase 7 audit records
    records = await audit_repo.list_all(limit=200)
    assert len(records) >= 8, f"Step 12 Failed: Expected >= 8 audit records, got {len(records)}"

    chain_valid, issues = await audit_repo.verify_chain_integrity()
    assert chain_valid is True, f"Step 12 Failed: Cryptographic audit log hash chain validation failed: {issues}"
    assert len(issues) == 0, f"Step 12 Failed: Audit chain issues detected: {issues}"

    ordered = AuditLogChainVerifier.order_chain(records)
    assert len(ordered) == len(records), "Step 12 Failed: Audit record count mismatch"
    last_record = ordered[-1]
    assert last_record.signature is not None and len(last_record.signature) == 64, "Step 12 Failed: Invalid SHA-256 terminal chain hash"
