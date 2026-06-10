from __future__ import annotations

import logging
from typing import Any

from app.schemas.agent import ChatConfig
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

logger = logging.getLogger(__name__)


async def load_context_config() -> Any:
    """Build AgentScope ContextConfig from platform settings."""
    from agentscope.agent import ContextConfig
    from app.services.config_service import ConfigService

    async def _float(key: str, default: float) -> float:
        raw = await ConfigService.get(key)
        try:
            return float(raw) if raw not in (None, "") else default
        except (TypeError, ValueError):
            return default

    async def _int(key: str, default: int) -> int:
        raw = await ConfigService.get(key)
        try:
            return int(raw) if raw not in (None, "") else default
        except (TypeError, ValueError):
            return default

    trigger_ratio = await _float("agentscope_context_trigger_ratio", 0.8)
    reserve_ratio = await _float("agentscope_context_reserve_ratio", 0.1)
    tool_result_limit = await _int("agentscope_tool_result_limit", 2000)

    trigger_ratio = min(max(trigger_ratio, 0.5), 0.89)
    reserve_ratio = min(max(reserve_ratio, 0.05), trigger_ratio - 0.05)

    return ContextConfig(
        trigger_ratio=trigger_ratio,
        reserve_ratio=reserve_ratio,
        tool_result_limit=tool_result_limit,
    )


async def build_model_config(
    *,
    config: ChatConfig | None,
    primary_model_name: str,
) -> Any:
    """Build AgentScope ModelConfig with optional fallback model."""
    from agentscope.agent import ModelConfig
    from app.services.ai.config import AgentConfigProvider

    fallback_model = None
    try:
        fallback_handle = await AgentConfigProvider.get_fallback_llm(
            streaming=True,
            config=config,
            exclude_model=primary_model_name,
        )
        fallback_model = (
            getattr(fallback_handle, "native_model", None) if fallback_handle else None
        )
    except Exception as exc:
        logger.warning("[agent_runtime] Failed to load fallback model: %s", exc)
    return ModelConfig(fallback_model=fallback_model, max_retries=0)


def build_tools_fingerprint(
    config: ChatConfig,
    tools: list[RuntimeToolSpec],
) -> str:
    import hashlib
    import json

    tool_names = sorted(spec.name for spec in tools)
    payload = {
        "agent_name": config.agent_name,
        "agent_version": config.agent_version,
        "model_name": config.model_name,
        "tools": tool_names,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
