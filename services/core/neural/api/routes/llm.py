from fastapi import APIRouter, Depends, HTTPException
from neural.api.deps import get_llm_gateway
from neural.api.schemas.requests import LLMGenerateRequest
from neural.api.schemas.responses import LLMTelemetryResponse
from neural.db.engine import get_db_session
from neural.exceptions import ModelProviderError
from neural.llm.gateway import LLMGateway, sanitize_error_message
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/llm", tags=["LLM Gateway"])


@router.get("/telemetry", response_model=LLMTelemetryResponse)
async def get_llm_telemetry(gateway: LLMGateway = Depends(get_llm_gateway)):
    summary = gateway.cost_tracker.get_all_usage_summary()
    return LLMTelemetryResponse(
        total_calls=summary["total_calls"],
        total_input_tokens=summary["total_input_tokens"],
        total_output_tokens=summary["total_output_tokens"],
        total_tokens=summary["total_tokens"],
        total_cost_usd=summary["total_cost_usd"],
        by_model=summary.get("by_model", {})
    )


@router.get("/telemetry/projects/{project_id}")
async def get_project_telemetry(
    project_id: str,
    gateway: LLMGateway = Depends(get_llm_gateway)
):
    return gateway.cost_tracker.get_project_cost(project_id)


@router.get("/telemetry/employees/{employee_id}")
async def get_employee_telemetry(
    employee_id: str,
    gateway: LLMGateway = Depends(get_llm_gateway)
):
    return gateway.cost_tracker.get_employee_cost(employee_id)


@router.get("/telemetry/tasks/{task_id}")
async def get_task_telemetry(
    task_id: str,
    gateway: LLMGateway = Depends(get_llm_gateway)
):
    return gateway.cost_tracker.get_task_cost(task_id)


@router.get("/routes")
async def get_model_routes(gateway: LLMGateway = Depends(get_llm_gateway)):
    breakers_status = {
        name: {
            "state": breaker.state.value,
            "consecutive_failures": breaker._consecutive_failures
        }
        for name, breaker in gateway.circuit_breakers.items()
    }
    return {
        "tiers": gateway.tiers_config,
        "routes": gateway.routes_config,
        "circuit_breakers": breakers_status
    }


@router.post("/generate")
async def generate_completion(
    request: LLMGenerateRequest,
    gateway: LLMGateway = Depends(get_llm_gateway),
    session: AsyncSession = Depends(get_db_session)
):
    try:
        response = await gateway.generate(
            prompt=request.prompt,
            tier=request.tier,
            route=request.route,
            model=request.model,
            system_prompt=request.system_prompt,
            employee_id=request.employee_id,
            task_id=request.task_id,
            project_id=request.project_id,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            db_session=session
        )
        return {
            "content": response.content,
            "model": response.model,
            "tier": response.tier,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "provider": response.provider,
            "latency_seconds": response.latency_seconds,
            "metadata": response.metadata
        }
    except ModelProviderError as err:
        raise HTTPException(status_code=502, detail=sanitize_error_message(str(err)))
