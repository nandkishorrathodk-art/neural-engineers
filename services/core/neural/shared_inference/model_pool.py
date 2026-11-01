from __future__ import annotations

from neural.decision_intelligence.system_one.laya_adapter import LayaAdapter
from neural.decision_intelligence.system_one.model_registry import get_model_registry
from neural.llm.gateway import LLMGateway
from neural.logger import get_logger

logger = get_logger("ancos.shared_inference.model_pool")


class SharedModelPool:
    """
    Shared Model Pool & Weight Multiplexer.
    Avoids loading duplicate neural network checkpoints or client sessions
    for every employee in the workforce. Preserves per-employee request isolation.
    """

    def __init__(self) -> None:
        self._laya_adapters: dict[str, LayaAdapter] = {}
        self._llm_gateways: dict[str, LLMGateway] = {}
        self._registry = get_model_registry()

    def get_laya_adapter(self, checkpoint_name: str = "Laya-Typed-Decisions") -> LayaAdapter:
        if checkpoint_name not in self._laya_adapters:
            logger.info("Initializing shared Laya model adapter singleton", checkpoint=checkpoint_name)
            self._laya_adapters[checkpoint_name] = LayaAdapter(
                checkpoint_name=checkpoint_name, registry=self._registry
            )
        return self._laya_adapters[checkpoint_name]

    def get_llm_gateway(self, tier: str = "default") -> LLMGateway:
        if tier not in self._llm_gateways:
            logger.info("Initializing shared LLM Gateway singleton", tier=tier)
            self._llm_gateways[tier] = LLMGateway()
        return self._llm_gateways[tier]

    def count_loaded_models(self) -> int:
        return len(self._laya_adapters) + len(self._llm_gateways)

