from __future__ import annotations

from typing import Optional

from neural.exceptions import EmergencyStopTriggered, HumanApprovalRequiredError
from neural.gui_intelligence.models import GUIActionRequest
from neural.gui_intelligence.safety.gui_action_policy import GUIActionPolicy
from neural.logger import get_logger
from neural.security.approval_gates import HumanApprovalGate
from neural.security.kill_switch import GlobalEmergencyKillSwitch, get_kill_switch

logger = get_logger("ancos.gui_intelligence.safety.risk_guard")


class GUIRiskGuard:
    """
    GUI Risk Guard & Interception Gate (Section 17).
    Integrates directly with:
    - GlobalEmergencyKillSwitch: instant halt (<200ms) on emergency kill signal.
    - HumanApprovalGate: mandates human approval for critical destructive operations.
    """

    def __init__(
        self,
        policy: Optional[GUIActionPolicy] = None,
        kill_switch: Optional[GlobalEmergencyKillSwitch] = None,
        approval_gate: Optional[HumanApprovalGate] = None,
    ) -> None:
        self.policy = policy or GUIActionPolicy()
        try:
            self.kill_switch = kill_switch or get_kill_switch()
        except Exception:
            self.kill_switch = kill_switch or GlobalEmergencyKillSwitch()
        self.approval_gate = approval_gate or HumanApprovalGate()

    def guard_action(self, request: GUIActionRequest) -> None:
        """
        Enforces safety policies before dispatching any GUI action.
        Raises:
        - EmergencyStopTriggered if global kill switch is active.
        - HumanApprovalRequiredError if action is critical/destructive and not approved.
        - StrictGUIContractViolation if forbidden shortcuts are used.
        """
        # 1. Emergency Kill Switch check (<200ms fast halt)
        if self.kill_switch.is_halted:
            logger.error("GUI action blocked by active Emergency Kill Switch", action_id=request.action_id)
            raise EmergencyStopTriggered("Global Emergency Stop active; GUI action dispatch suspended.")

        # 2. Strict GUI Contract check
        context = f"{request.action_type} {request.text_content or ''} {request.key_code or ''} {request.action_id}"
        self.policy.validate_gui_contract(context)

        # 3. Risk classification
        risk = self.policy.classify_risk(request)

        # 4. Human Approval Gate for CRITICAL actions
        if risk == "CRITICAL":
            # Check destructive keywords
            details = request.text_content or request.action_type
            if HumanApprovalGate.is_destructive(request.action_type, {"text": details}) or HumanApprovalGate.is_destructive(details):
                logger.warning("Critical GUI action requires human approval", action_id=request.action_id)
                raise HumanApprovalRequiredError(
                    action_type=f"GUI_{request.action_type}",
                    details=f"Destructive action detected: {details}",
                )

        # 5. Standard policy permissions
        self.policy.check_permission(request)
