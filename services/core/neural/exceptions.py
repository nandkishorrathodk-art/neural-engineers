class ANCOSError(Exception):
    """Base exception for all ANCOS errors."""
    pass


class ConfigurationError(ANCOSError):
    """Raised when configuration is invalid or missing."""
    pass


class SecurityViolationError(ANCOSError):
    """Raised when an operation violates sandbox or security policy."""
    pass


class EmergencyStopTriggered(ANCOSError):
    """Raised when an action is blocked due to the emergency stop switch."""
    pass


class HumanApprovalRequiredError(ANCOSError):
    """Raised when an action is halted awaiting explicit human approval."""
    def __init__(self, action_type: str, details: str):
        self.action_type = action_type
        self.details = details
        super().__init__(f"Action '{action_type}' requires human approval: {details}")


class ClarificationRequiredError(ANCOSError):
    """Raised when an owner objective is too ambiguous to plan safely."""

    def __init__(self, questions: list[str]):
        self.questions = questions
        super().__init__("Owner clarification is required before this objective can be planned.")


class ContainerError(ANCOSError):
    """Raised when container management or VNC gateway encounters an error."""
    pass


class StorageError(ANCOSError):
    """Raised when storage or backup operations fail."""
    pass


class QuotaExceededError(ANCOSError):
    """Raised when employee count, memory, or budget quota is exceeded."""
    pass


class TaskDependencyError(ANCOSError):
    """Raised when task DAG contains cycles or unresolvable dependencies."""
    pass


class VerificationFailedError(ANCOSError):
    """Raised when independent QA fails task verification."""
    pass


class ModelProviderError(ANCOSError):
    """Raised when an external or local LLM provider fails."""
    pass


class FabricatedProgressError(ANCOSError):
    """Raised when an employee progress report contains ungrounded or fabricated percentages/metrics."""
    pass


class UnverifiedTaskError(ANCOSError):
    """Raised when a task attempts to transition to completed without an independent verification receipt."""
    pass


class ArtifactNotFoundError(ANCOSError):
    """Raised when a requested artifact does not exist."""
    pass


class ArtifactIntegrityError(ANCOSError):
    """Raised when an artifact checksum verification fails (corrupted or tampered)."""
    pass


class RetriesExhaustedError(ANCOSError):
    """Raised when a task has exceeded its maximum retry limit and must be escalated."""
    pass


class InvalidTaskStateTransitionError(ANCOSError):
    """Raised when an illegal or unsupported state transition is attempted on a task."""
    pass


class RateLimitError(ModelProviderError):
    """Raised when an LLM provider rate limit is encountered."""
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class NetworkPerimeterViolation(SecurityViolationError):
    """Raised when network access to cloud metadata or unauthorized private network is attempted."""
    pass


class SyscallSecurityViolation(SecurityViolationError):
    """Raised when an unauthorized system call or OS boundary violation is attempted."""
    pass


class DiskQuotaExceededError(QuotaExceededError):
    """Raised when a workspace or process exceeds its allocated disk quota."""
    pass


class AuditIntegrityError(SecurityViolationError):
    """Raised when cryptographic audit log hash chain validation fails."""
    pass


class GUIIntelligenceError(ANCOSError):
    """Base exception for GUI intelligence errors."""
    pass


class GUIActionBlockedError(SecurityViolationError):
    """Raised when an action is blocked by GUI safety policy or destructive guard."""
    pass


class StrictGUIContractViolation(SecurityViolationError):
    """Raised when an attempt is made to bypass the strict GUI-only interaction contract (DOM, AT-SPI, etc.)."""
    pass


class FocusVerificationFailedError(GUIIntelligenceError):
    """Raised when keyboard text entry is attempted without verified visual focus cue."""
    pass


class VisualUncertaintyError(GUIIntelligenceError):
    """Raised when visual grounding uncertainty exceeds the confidence escort threshold."""
    pass


class VisualOcclusionError(GUIIntelligenceError):
    """Raised when a target UI element is occluded or not visible."""
    pass


class DecisionIntelligenceError(ANCOSError):
    """Base exception for Decision Intelligence errors."""
    pass


class ModelCheckpointError(DecisionIntelligenceError):
    """Raised when model checkpoint loading, verification, or hash mismatch fails."""
    pass


class EscalationRequiredError(DecisionIntelligenceError):
    """Raised when decision exceeds System 1 boundaries and requires System 2 escalation."""
    pass


class CalibrationDriftError(DecisionIntelligenceError):
    """Raised when calibration error ECE exceeds allowable threshold."""
    pass


class ShadowModeViolationError(DecisionIntelligenceError):
    """Raised when shadow mode operation produces an illegal side-effect."""
    pass


class BatchManagerError(DecisionIntelligenceError):
    """Raised when micro-batch manager encounters an internal processing error."""
    pass






