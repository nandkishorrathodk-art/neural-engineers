import secrets

from fastapi import Depends, Header, HTTPException
from neural.config import get_settings
from neural.db.engine import AsyncSessionLocal, get_db_session
from neural.db.repositories import (
    ActionItemRepository,
    ApprovalRepository,
    AuditRepository,
    ContainerRepository,
    DecisionLedgerRepository,
    DecisionRecordRepository,
    EmployeeRepository,
    EvolutionRepository,
    MeetingRepository,
    MemoryRepository,
    MethodBenchmarkRepository,
    MethodRepository,
    PerformanceRepository,
    ProjectRepository,
    ProposalRepository,
    SkillCertificationRepository,
    TaskRepository,
    ToolRepository,
    VoteRepository,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def get_project_repo(session: AsyncSession = Depends(get_db_session)) -> ProjectRepository:
    return ProjectRepository(session)


async def get_task_repo(session: AsyncSession = Depends(get_db_session)) -> TaskRepository:
    return TaskRepository(session)


async def require_worker_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    expected = settings.WORKER_API_TOKEN
    if not expected or len(expected) < 32:
        raise HTTPException(status_code=503, detail="Worker API credentials are not configured.")
    if settings.VERIFIER_API_TOKEN and secrets.compare_digest(expected, settings.VERIFIER_API_TOKEN):
        raise HTTPException(status_code=503, detail="Worker and verifier credentials must be different.")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Valid worker bearer token required.")
    return "worker-service"


async def require_verifier_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    expected = settings.VERIFIER_API_TOKEN
    if not expected or len(expected) < 32:
        raise HTTPException(status_code=503, detail="Verifier API credentials are not configured.")
    if any(
        other and secrets.compare_digest(expected, other)
        for other in (settings.WORKER_API_TOKEN, settings.OWNER_API_TOKEN)
    ):
        raise HTTPException(status_code=503, detail="Owner, worker, and verifier credentials must be different.")
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
    return OrganizationalMemoryStore(memory_repo=memory_repo)


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





