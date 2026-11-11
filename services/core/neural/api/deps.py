import secrets
from typing import Any

from fastapi import Depends, Header, HTTPException
from neural.config import get_settings
from neural.constants import SecurityRole
from neural.db.engine import AsyncSessionLocal, get_db_session
from neural.db.repositories import (
    ActionItemRepository,
    ApprovalRepository,
    AuditRepository,
    BudgetRepository,
    ContainerRepository,
    DecisionLedgerRepository,
    DecisionRecordRepository,
    EmergencyStopRepository,
    EmployeeRepository,
    EvolutionRepository,
    MeetingRepository,
    MemoryRepository,
    MethodBenchmarkRepository,
    MethodRepository,
    PerformanceRepository,
    ProjectRepository,
    ProposalRepository,
    ResourceLeaseRepository,
    ServiceHeartbeatRepository,
    SkillCertificationRepository,
    TaskRepository,
    ToolRepository,
    VoteRepository,
)
from neural.security.kill_switch import get_kill_switch
from sqlalchemy.ext.asyncio import AsyncSession


async def get_project_repo(session: AsyncSession = Depends(get_db_session)) -> ProjectRepository:
    return ProjectRepository(session)


async def get_task_repo(session: AsyncSession = Depends(get_db_session)) -> TaskRepository:
    return TaskRepository(session)


async def get_resource_lease_repo(
    session: AsyncSession = Depends(get_db_session),
) -> ResourceLeaseRepository:
    return ResourceLeaseRepository(session)


async def get_budget_repo(session: AsyncSession = Depends(get_db_session)) -> BudgetRepository:
    return BudgetRepository(session)


async def require_execution_not_halted(
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Read the shared emergency-stop latch before any worker/verifier operation."""
    try:
        state = await EmergencyStopRepository(session).get_state()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Emergency-stop state is unavailable; worker and verifier execution is blocked.",
        ) from exc
    get_kill_switch().sync_state(state)
    if state["active"]:
        raise HTTPException(
            status_code=503,
            detail=f"Emergency stop is active: {state['reason'] or 'operator halt'}.",
        )


def validate_all_role_credentials(settings: Any) -> None:
    """
    Validates pairwise credential distinctness and format across all operational roles:
    Owner, Boss, Worker, Verifier, Security Reviewer (+ Approver).
    Raises HTTPException(status_code=503) if any credentials overlap.
    """
    role_tokens: dict[str, list[str]] = {
        SecurityRole.OWNER.value: [settings.OWNER_API_TOKEN] if settings.OWNER_API_TOKEN else [],
        SecurityRole.BOSS.value: [settings.BOSS_API_TOKEN] if settings.BOSS_API_TOKEN else [],
        SecurityRole.VERIFIER.value: [settings.VERIFIER_API_TOKEN] if settings.VERIFIER_API_TOKEN else [],
        SecurityRole.SECURITY_REVIEWER.value: [settings.SECURITY_REVIEWER_API_TOKEN] if settings.SECURITY_REVIEWER_API_TOKEN else [],
        "approver": [settings.APPROVER_API_TOKEN] if settings.APPROVER_API_TOKEN else [],
        SecurityRole.WORKER.value: [t for t in settings.WORKER_API_TOKENS.values() if t],
    }
    if settings.WORKER_API_TOKEN:
        role_tokens[SecurityRole.WORKER.value].append(settings.WORKER_API_TOKEN)

    worker_tokens = role_tokens[SecurityRole.WORKER.value]
    if len(worker_tokens) > len(set(worker_tokens)):
        raise HTTPException(status_code=503, detail="Worker identities must use distinct bearer credentials.")

    roles = list(role_tokens.keys())
    for i, role_a in enumerate(roles):
        for token_a in role_tokens[role_a]:
            if not token_a:
                continue
            for role_b in roles[i + 1:]:
                for token_b in role_tokens[role_b]:
                    if not token_b:
                        continue
                    if secrets.compare_digest(token_a, token_b):
                        raise HTTPException(
                            status_code=503,
                            detail=f"Role credentials must be pairwise distinct: '{role_a}' and '{role_b}' share credentials.",
                        )


async def require_worker_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    validate_all_role_credentials(settings)
    if any(
        not worker_id.strip() or len(worker_id) > 128 or not token or len(token) < 32
        for worker_id, token in settings.WORKER_API_TOKENS.items()
    ):
        raise HTTPException(
            status_code=503,
            detail="Every configured worker identity must have a name and a token of at least 32 characters.",
        )
    configured = {
        worker_id: token
        for worker_id, token in settings.WORKER_API_TOKENS.items()
    }
    if settings.WORKER_API_TOKEN and len(settings.WORKER_API_TOKEN) >= 32:
        configured.setdefault("worker-service", settings.WORKER_API_TOKEN)
    if not configured:
        raise HTTPException(status_code=503, detail="Worker API credentials are not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="Valid worker bearer token required.")
    for worker_id, expected in configured.items():
        if secrets.compare_digest(supplied, expected):
            return worker_id
    raise HTTPException(status_code=401, detail="Valid worker bearer token required.")


async def require_owner_identity(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    validate_all_role_credentials(settings)
    if settings.APP_ENV != "production":
        return "local-owner"
    expected = settings.OWNER_API_TOKEN
    if not expected or len(expected) < 32:
        raise HTTPException(status_code=503, detail="Owner API credentials are not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid owner bearer token required.")
    return "human-owner"


async def require_boss_identity(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    validate_all_role_credentials(settings)
    if settings.APP_ENV != "production" and not authorization:
        return "local-boss"
    expected = settings.BOSS_API_TOKEN
    if not expected or len(expected) < 32:
        raise HTTPException(status_code=503, detail="Boss API credentials are not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid boss bearer token required.")
    return "boss-ai"


async def require_boss_or_owner_identity(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    validate_all_role_credentials(settings)
    if settings.APP_ENV != "production" and not authorization:
        return "local-boss"
    boss_token = settings.BOSS_API_TOKEN
    owner_token = settings.OWNER_API_TOKEN
    if (not boss_token or len(boss_token) < 32) and (not owner_token or len(owner_token) < 32):
        raise HTTPException(status_code=503, detail="Boss or Owner API credentials are not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="Valid boss or owner bearer token required.")
    if boss_token and len(boss_token) >= 32 and secrets.compare_digest(supplied, boss_token):
        return "boss-ai"
    if owner_token and len(owner_token) >= 32 and secrets.compare_digest(supplied, owner_token):
        return "human-owner"
    raise HTTPException(status_code=401, detail="Valid boss or owner bearer token required.")


async def require_security_reviewer_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    validate_all_role_credentials(settings)
    if settings.APP_ENV != "production" and not authorization:
        return "local-security-reviewer"
    expected = settings.SECURITY_REVIEWER_API_TOKEN
    if not expected or len(expected) < 32:
        raise HTTPException(status_code=503, detail="Security Reviewer API credentials are not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid security reviewer bearer token required.")
    return "security-reviewer"


async def require_owner_or_security_reviewer_identity(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    validate_all_role_credentials(settings)
    if settings.APP_ENV != "production" and not authorization:
        return "local-owner"
    owner_token = settings.OWNER_API_TOKEN
    sec_token = settings.SECURITY_REVIEWER_API_TOKEN
    if (not owner_token or len(owner_token) < 32) and (not sec_token or len(sec_token) < 32):
        raise HTTPException(status_code=503, detail="Owner or Security Reviewer API credentials are not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="Valid owner or security reviewer bearer token required.")
    if owner_token and len(owner_token) >= 32 and secrets.compare_digest(supplied, owner_token):
        return "human-owner"
    if sec_token and len(sec_token) >= 32 and secrets.compare_digest(supplied, sec_token):
        return "security-reviewer"
    raise HTTPException(status_code=401, detail="Valid owner or security reviewer bearer token required.")


async def require_approver_identity(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    validate_all_role_credentials(settings)
    if settings.APP_ENV != "production" and not authorization:
        return "local-approver"
    expected = settings.APPROVER_API_TOKEN
    if not expected or len(expected) < 32:
        raise HTTPException(status_code=503, detail="Independent approver credentials are not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid approver bearer token required.")
    return "independent-approver"


async def require_verifier_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    validate_all_role_credentials(settings)
    expected = settings.VERIFIER_API_TOKEN
    if not expected or len(expected) < 32:
        raise HTTPException(status_code=503, detail="Verifier API credentials are not configured.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid verifier bearer token required.")
    return "verification-service"


async def get_employee_repo(session: AsyncSession = Depends(get_db_session)) -> EmployeeRepository:
    return EmployeeRepository(session)


async def get_container_repo(session: AsyncSession = Depends(get_db_session)) -> ContainerRepository:
    return ContainerRepository(session)


async def get_meeting_repo(session: AsyncSession = Depends(get_db_session)) -> MeetingRepository:
    return MeetingRepository(session)


async def get_action_item_repo(session: AsyncSession = Depends(get_db_session)) -> ActionItemRepository:
    return ActionItemRepository(session)


async def get_approval_repo(session: AsyncSession = Depends(get_db_session)) -> ApprovalRepository:
    return ApprovalRepository(session)


async def get_perf_repo(session: AsyncSession = Depends(get_db_session)) -> PerformanceRepository:
    return PerformanceRepository(session)


async def get_service_heartbeat_repo(
    session: AsyncSession = Depends(get_db_session),
) -> ServiceHeartbeatRepository:
    return ServiceHeartbeatRepository(session)


async def get_audit_repo(session: AsyncSession = Depends(get_db_session)) -> AuditRepository:
    return AuditRepository(session)


async def get_message_repo(session: AsyncSession = Depends(get_db_session)):
    from neural.db.repositories import MessageRepository
    return MessageRepository(session)


async def get_proposal_repo(session: AsyncSession = Depends(get_db_session)) -> ProposalRepository:
    return ProposalRepository(session)


async def get_vote_repo(session: AsyncSession = Depends(get_db_session)) -> VoteRepository:
    return VoteRepository(session)


async def get_ledger_repo(session: AsyncSession = Depends(get_db_session)) -> DecisionLedgerRepository:
    return DecisionLedgerRepository(session)


async def get_skill_cert_repo(session: AsyncSession = Depends(get_db_session)) -> SkillCertificationRepository:
    return SkillCertificationRepository(session)


async def get_evolution_repo(session: AsyncSession = Depends(get_db_session)) -> EvolutionRepository:
    return EvolutionRepository(session)


async def get_tool_repo(session: AsyncSession = Depends(get_db_session)) -> ToolRepository:
    return ToolRepository(session)


async def get_method_repo(session: AsyncSession = Depends(get_db_session)) -> MethodRepository:
    return MethodRepository(session)


async def get_method_benchmark_repo(session: AsyncSession = Depends(get_db_session)) -> MethodBenchmarkRepository:
    return MethodBenchmarkRepository(session)


async def get_memory_repo(session: AsyncSession = Depends(get_db_session)) -> MemoryRepository:
    return MemoryRepository(session)


async def get_toolsmith_engine(
    tool_repo: ToolRepository = Depends(get_tool_repo),
):
    from neural.tools.engine import ToolsmithEngine
    from neural.tools.registry import ToolRegistry
    return ToolsmithEngine(tool_repo=tool_repo, registry=ToolRegistry(tool_repo=tool_repo))


async def get_method_lab(
    method_repo: MethodRepository = Depends(get_method_repo),
    bmk_repo: MethodBenchmarkRepository = Depends(get_method_benchmark_repo),
):
    from neural.methods.catalog import MethodologyCatalog
    from neural.methods.lab import MethodLabEngine
    return MethodLabEngine(method_repo=method_repo, benchmark_repo=bmk_repo, catalog=MethodologyCatalog(method_repo=method_repo))


async def get_memory_store(
    memory_repo: MemoryRepository = Depends(get_memory_repo),
):
    from neural.memory.store import OrganizationalMemoryStore
    store = OrganizationalMemoryStore(memory_repo=memory_repo)
    # OrganizationalMemoryStore writes versioned records through MemoryRepository;
    # connect that repository for hydration on this fresh request-scoped instance.
    store.six_tier_store.memory_repo = memory_repo
    await store.six_tier_store.load_persisted_records()
    return store


async def get_self_improvement_engine(
    evolution_repo: EvolutionRepository = Depends(get_evolution_repo),
    task_repo: TaskRepository = Depends(get_task_repo),
    employee_repo: EmployeeRepository = Depends(get_employee_repo),
    memory_store=Depends(get_memory_store),
    toolsmith=Depends(get_toolsmith_engine),
    method_lab=Depends(get_method_lab),
):
    from neural.evolution.meta_learning import RecursiveSelfImprovementEngine
    return RecursiveSelfImprovementEngine(
        evolution_repo=evolution_repo,
        task_repo=task_repo,
        employee_repo=employee_repo,
        memory_store=memory_store,
        toolsmith=toolsmith,
        method_lab=method_lab,
    )




async def get_governance_engine(
    session: AsyncSession = Depends(get_db_session)
):
    from neural.governance.committee import BoardroomCommittee
    from neural.governance.deliberation import DeliberationEngine
    from neural.governance.engine import BoardroomGovernanceEngine

    prop_repo = ProposalRepository(session)
    vote_repo = VoteRepository(session)
    ledger_repo = DecisionLedgerRepository(session)
    task_repo = TaskRepository(session)
    proj_repo = ProjectRepository(session)
    audit_repo = AuditRepository(session)
    bus = get_message_bus()
    llm = get_llm_gateway()
    deliberation = DeliberationEngine(llm_gateway=llm, message_bus=bus)

    return BoardroomGovernanceEngine(
        proposal_repo=prop_repo,
        vote_repo=vote_repo,
        ledger_repo=ledger_repo,
        committee=BoardroomCommittee(),
        deliberation_engine=deliberation,
        task_repo=task_repo,
        project_repo=proj_repo,
        message_bus=bus,
        audit_repo=audit_repo
    )



_container_manager_instance = None


def get_container_manager():
    global _container_manager_instance
    if _container_manager_instance is None:
        from neural.containers.manager import ContainerManager
        _container_manager_instance = ContainerManager()
    return _container_manager_instance


_message_bus_instance = None


def get_message_bus():
    global _message_bus_instance
    if _message_bus_instance is None:
        from neural.comms.bus import MessageBus
        settings = get_settings()
        if settings.APP_ENV == "production":
            _message_bus_instance = MessageBus(
                prefer_in_memory=False,
                session_factory=AsyncSessionLocal,
            )
        else:
            _message_bus_instance = MessageBus(prefer_in_memory=True)
    return _message_bus_instance


_llm_gateway_instance = None


def get_llm_gateway():
    global _llm_gateway_instance
    if _llm_gateway_instance is None:
        from neural.llm.gateway import LLMGateway
        _llm_gateway_instance = LLMGateway()
    return _llm_gateway_instance


async def get_decision_record_repo(session: AsyncSession = Depends(get_db_session)) -> DecisionRecordRepository:
    return DecisionRecordRepository(session)





