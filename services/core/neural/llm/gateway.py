import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional, Type, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from neural.config import get_settings
from neural.exceptions import ModelProviderError, RateLimitError
from neural.llm.cost import CostTracker
from neural.llm.retry import CircuitBreaker, retry_with_backoff
from neural.logger import get_logger

logger = get_logger("ancos.llm.gateway")

T = TypeVar("T", bound=BaseModel)


def extract_json_from_text(text: str) -> dict[str, Any]:
    """
    Robustly extracts and parses a JSON object from text, handling
    markdown code blocks (```json ... ``` or ``` ... ```) or embedded JSON objects.
    """
    clean_text = text.strip()

    # Case 1: Specific ```json code block(s)
    json_blocks = re.findall(r"```json\s*([\s\S]*?)\s*```", clean_text)
    for block in json_blocks:
        try:
            return json.loads(block.strip())
        except Exception:
            continue

    # Case 2: Any markdown code block ``` ... ```
    all_blocks = re.findall(r"```\s*([\s\S]*?)\s*```", clean_text)
    for block in all_blocks:
        try:
            return json.loads(block.strip())
        except Exception:
            continue

    # Case 3: Direct json parse
    try:
        return json.loads(clean_text)
    except Exception:
        pass

    # Case 4: Embedded JSON between outermost { and }
    start_brace = clean_text.find("{")
    end_brace = clean_text.rfind("}")
    if start_brace != -1 and end_brace > start_brace:
        snippet = clean_text[start_brace : end_brace + 1]
        try:
            return json.loads(snippet)
        except Exception as e:
            raise ValueError(f"Failed to parse embedded JSON from text: {e}") from e

    raise ValueError(f"No valid JSON object could be extracted from response text: {clean_text[:200]}")


@dataclass
class LLMResponse:
    content: str
    model: str
    tier: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    provider: str
    latency_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMGateway:
    """
    Unified Multi-Provider LLM Gateway.
    Implements tier-based routing, transparent fallback, circuit breaking,
    exponential retry backoff, rate limit handling, and USD cost tracking per employee, task, and project.
    """

    def __init__(
        self,
        config_path: str = "configs/model-routing.yaml",
        cost_tracker: Optional[CostTracker] = None,
        provider_invoker: Optional[Callable[..., Coroutine[Any, Any, dict[str, Any]]]] = None
    ):
        self.config_path = config_path
        self.settings = get_settings()
        self.cost_tracker = cost_tracker or CostTracker()
        self.custom_invoker = provider_invoker
        self.circuit_breakers: dict[str, CircuitBreaker] = {}

        self.tiers_config: dict[str, dict[str, Any]] = {}
        self.routes_config: dict[str, str] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Loads routing and tier definitions from YAML configuration file."""
        search_paths = [
            self.config_path,
            os.path.abspath(self.config_path),
            os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", self.config_path)),
        ]
        target_path = None
        for p in search_paths:
            if os.path.exists(p) and os.path.isfile(p):
                target_path = p
                break

        if target_path:
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                    self.tiers_config = data.get("tiers", {})
                    self.routes_config = data.get("routes", {})
                    logger.info(
                        "Loaded model routing configuration",
                        tiers_count=len(self.tiers_config),
                        routes_count=len(self.routes_config),
                        path=target_path
                    )
            except Exception as e:
                logger.warning("Failed to load model routing config, using defaults", error=str(e))
                self._load_fallback_defaults()
        else:
            self._load_fallback_defaults()

    def _load_fallback_defaults(self) -> None:
        self.tiers_config = {
            "reasoning": {
                "primary": "claude-3-7-sonnet",
                "fallbacks": ["gpt-4o", "gemini-1.5-pro"]
            },
            "coding": {
                "primary": "claude-3-5-sonnet-20241022",
                "fallbacks": ["gpt-4o", "qwen-2.5-coder-32b"]
            },
            "fast": {
                "primary": "gpt-4o-mini",
                "fallbacks": ["claude-3-5-haiku", "gemini-1.5-flash"]
            }
        }
        self.routes_config = {
            "boss_brain": "reasoning",
            "task_decomposer": "reasoning",
            "meeting_moderator": "reasoning",
            "independent_verifier": "reasoning",
            "code_generation": "coding",
            "code_debugging": "coding",
            "code_review": "reasoning",
            "status_reporting": "fast",
            "log_summarization": "fast"
        }

    def get_circuit_breaker(self, model: str) -> CircuitBreaker:
        """Retrieves or creates a circuit breaker for a given model."""
        if model not in self.circuit_breakers:
            self.circuit_breakers[model] = CircuitBreaker(name=model, failure_threshold=3, recovery_timeout=30.0)
        return self.circuit_breakers[model]

    def get_models_for_tier(self, tier: str) -> list[str]:
        """Returns ordered candidate models [primary, fallback_1, fallback_2, ...] for a tier."""
        tier_data = self.tiers_config.get(tier, {})
        primary = tier_data.get("primary")
        fallbacks = tier_data.get("fallbacks", [])
        models: list[str] = []
        if primary:
            models.append(primary)
        for fb in fallbacks:
            if fb not in models:
                models.append(fb)
        return models or ["gpt-4o-mini"]

    def resolve_tier_for_route(self, route: str) -> str:
        """Resolves functional route name to corresponding tier."""
        return self.routes_config.get(route, "fast")

    def _is_rate_limit(self, err: Exception) -> bool:
        """Checks if an error represents an API rate limit or quota exhaustion."""
        msg = str(err).lower()
        return (
            "rate limit" in msg
            or "ratelimit" in msg
            or "429" in msg
            or "quota exceeded" in msg
            or "too many requests" in msg
        )

    async def _invoke_provider(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2000
    ) -> dict[str, Any]:
        """
        Invokes model provider. Uses custom invoker if provided, or litellm.
        """
        if self.custom_invoker:
            try:
                return await self.custom_invoker(
                    model=model,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
            except Exception as e:
                if self._is_rate_limit(e):
                    raise RateLimitError(f"Rate limit exceeded for model {model}: {e}") from e
                raise

        # Standard execution via LiteLLM
        try:
            import litellm
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            # Check if any provider API key is configured
            has_keys = any([
                self.settings.OPENAI_API_KEY,
                self.settings.ANTHROPIC_API_KEY,
                self.settings.GEMINI_API_KEY,
                self.settings.NVIDIA_API_KEY,
                os.getenv("OPENAI_API_KEY"),
                os.getenv("ANTHROPIC_API_KEY"),
                os.getenv("GEMINI_API_KEY"),
                os.getenv("NVIDIA_API_KEY")
            ])

            if not has_keys:
                # Simulated offline development response if no API keys are present
                in_tok = max(10, len(prompt.split()))
                out_tok = 50
                return {
                    "content": f"[Simulated Response from {model}] Processed prompt of {len(prompt)} characters.",
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "provider": model.split("-")[0]
                }

            response = None
            if model.startswith("nim/"):
                nvidia_api_key = self.settings.NVIDIA_API_KEY or os.getenv("NVIDIA_API_KEY")
                if not nvidia_api_key:
                    raise ModelProviderError("NVIDIA_API_KEY is required for NVIDIA NIM models.")
                # ``nim/`` is an internal routing prefix; NVIDIA expects its
                # catalog model ID without that prefix.
                nim_model = model[len("nim/"):]
                from openai import AsyncOpenAI

                extra_body: dict[str, Any] = {}
                if "nemotron-3" in nim_model:
                    # Keep reasoning models from spending a small completion
                    # budget only on hidden reasoning and returning no answer.
                    extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
                async with AsyncOpenAI(
                    api_key=nvidia_api_key,
                    base_url=self.settings.NVIDIA_API_BASE,
                ) as client:
                    response = await client.chat.completions.create(
                        model=nim_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **({"extra_body": extra_body} if extra_body else {}),
                    )
            else:
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            in_tok = usage.prompt_tokens if usage else len(prompt.split())
            out_tok = usage.completion_tokens if usage else len(content.split())
            provider = getattr(response, "model", model)

            return {
                "content": content,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "provider": "nvidia" if model.startswith("nim/") else provider
            }
        except Exception as e:
            if self._is_rate_limit(e):
                raise RateLimitError(f"Rate limit exceeded for {model}: {e}") from e
            raise ModelProviderError(f"Provider invocation error for {model}: {e}") from e

    async def generate(
        self,
        prompt: str,
        tier: Optional[str] = None,
        route: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        employee_id: Optional[str] = None,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        db_session: Optional[Any] = None
    ) -> LLMResponse:
        """
        Routes and generates LLM response with transparent fallback across tier models.
        Tracks cost and circuit state for every invocation.
        """
        effective_tier = tier or (self.resolve_tier_for_route(route) if route else "reasoning")
        if model:
            candidates = [model]
        else:
            candidates = self.get_models_for_tier(effective_tier)

        last_error: Optional[Exception] = None
        attempted_models: list[str] = []

        for candidate_model in candidates:
            breaker = self.get_circuit_breaker(candidate_model)
            if not breaker.can_execute():
                logger.warning(
                    "Skipping model because circuit breaker is OPEN",
                    model=candidate_model,
                    tier=effective_tier
                )
                continue

            attempted_models.append(candidate_model)
            start_time = time.time()

            try:
                async def _call():
                    return await self._invoke_provider(
                        model=candidate_model,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens
                    )

                result = await retry_with_backoff(
                    _call,
                    max_retries=1,
                    initial_delay=0.1,
                    retryable_exceptions=(ModelProviderError,)
                )

                latency = time.time() - start_time
                breaker.record_success()

                in_tokens = result.get("input_tokens", 0)
                out_tokens = result.get("output_tokens", 0)
                content = result.get("content", "")
                provider = result.get("provider", candidate_model)

                # Record cost and tokens (including project_id)
                record = self.cost_tracker.record_usage(
                    model=candidate_model,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    task_id=task_id,
                    employee_id=employee_id,
                    project_id=project_id
                )

                # If db_session provided, persist to DB performance records
                if db_session and task_id and employee_id:
                    try:
                        await self.cost_tracker.persist_to_db(
                            session=db_session,
                            task_id=task_id,
                            employee_id=employee_id,
                            model=candidate_model,
                            input_tokens=in_tokens,
                            output_tokens=out_tokens,
                            cost_usd=record.cost_usd
                        )
                    except Exception as db_err:
                        logger.warning("Could not persist performance record to DB", error=str(db_err))

                return LLMResponse(
                    content=content,
                    model=candidate_model,
                    tier=effective_tier,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    cost_usd=record.cost_usd,
                    provider=provider,
                    latency_seconds=round(latency, 4),
                    metadata={"attempted_models": attempted_models}
                )

            except Exception as err:
                last_error = err
                breaker.record_failure(err)
                logger.warning(
                    "Model invocation failed; falling back to next candidate",
                    model=candidate_model,
                    tier=effective_tier,
                    error=str(err)
                )

        raise ModelProviderError(
            f"All candidate models failed for tier '{effective_tier}'. "
            f"Attempted: {attempted_models}. Last error: {last_error}"
        )

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        tier: Optional[str] = None,
        route: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        employee_id: Optional[str] = None,
        task_id: Optional[str] = None,
        project_id: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        db_session: Optional[Any] = None
    ) -> tuple[T, LLMResponse]:
        """
        Generates structured output validated against a Pydantic model schema.
        Automatically instructs the model on required schema and extracts parsed JSON.
        Transparently falls back to secondary models if output cannot be validated.
        """
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        structured_instruction = (
            f"\n\nYou MUST respond ONLY with a valid JSON object matching the following JSON Schema:\n"
            f"```json\n{schema_json}\n```\n"
            f"Do not include any other explanations, prose, or markdown other than the JSON object."
        )
        effective_system = (system_prompt or "") + structured_instruction

        effective_tier = tier or (self.resolve_tier_for_route(route) if route else "reasoning")
        candidates = [model] if model else self.get_models_for_tier(effective_tier)

        last_error: Optional[Exception] = None
        for candidate_model in candidates:
            try:
                response = await self.generate(
                    prompt=prompt,
                    tier=effective_tier,
                    model=candidate_model,
                    system_prompt=effective_system,
                    employee_id=employee_id,
                    task_id=task_id,
                    project_id=project_id,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    db_session=db_session
                )
                parsed_data = extract_json_from_text(response.content)
                validated = response_model.model_validate(parsed_data)
                return validated, response
            except (ValueError, ValidationError) as parse_err:
                last_error = parse_err
                logger.warning(
                    "Structured output parsing failed, trying next candidate model if available",
                    model=candidate_model,
                    error=str(parse_err)
                )
            except Exception as gen_err:
                last_error = gen_err
                logger.warning(
                    "Model generation failed during structured output, trying next model",
                    model=candidate_model,
                    error=str(gen_err)
                )

        raise ModelProviderError(
            f"Failed to extract structured response for {response_model.__name__}: {last_error}"
        )


