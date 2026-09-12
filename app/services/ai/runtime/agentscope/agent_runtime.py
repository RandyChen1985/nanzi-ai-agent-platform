from __future__ import annotations

import logging
from typing import Any

from app.schemas.agent import ChatConfig
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

logger = logging.getLogger(__name__)


def _config_flag_enabled(raw: Any, *, default: bool = True) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


async def load_injection_config(*, inject_runtime_state: bool | None = None) -> Any:
    """Build AgentScope InjectionConfig with platform timezone.

    AgentScope 默认已开启 runtime state 注入（时区 UTC）。此处显式绑定
    ``platform_timezone``；可用系统配置 ``agentscope_inject_runtime_state``
    关闭。可选 ``agentscope_inject_time_interval_hours`` 控制时间重复注入间隔。
    不改变工具链 / HITL，仅影响上下文 hint。
    """
    from agentscope.agent import InjectionConfig
    from app.services.config_service import ConfigService
    from app.services.platform_timezone import get_cached_platform_timezone

    keys = {"agentscope_inject_time_interval_hours": "0.5"}
    if inject_runtime_state is None:
        keys["agentscope_inject_runtime_state"] = "true"

    configs = await ConfigService.get_many(keys)

    enabled = inject_runtime_state
    if enabled is None:
        raw = configs.get("agentscope_inject_runtime_state")
        enabled = _config_flag_enabled(raw, default=True)

    interval_raw = configs.get("agentscope_inject_time_interval_hours")
    try:
        time_interval = float(interval_raw) if interval_raw not in (None, "") else 0.5
    except (TypeError, ValueError):
        time_interval = 0.5
    time_interval = min(max(time_interval, 0.0), 24.0)

    return InjectionConfig(
        inject_runtime_state=bool(enabled),
        timezone=get_cached_platform_timezone(),
        time_interval=time_interval,
    )


def build_runtime_middlewares(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    agent_name: str | None = None,
    trace_id: str | None = None,
) -> list[Any]:
    """Assemble Agent middlewares: forbidden-tool DENY + audit + model-call stats."""
    from app.services.ai.runtime.agentscope.middleware import (
        BashSandboxParentLinkMiddleware,
        ModelCallStatsMiddleware,
        ToolPermissionMiddleware,
    )
    from app.core.context import get_current_agent_context

    runtime_context = get_current_agent_context()
    runtime_info = dict(
        getattr(runtime_context, "runtime_model_info", {}) or {}
    ) if runtime_context else {}

    async def _forbidden_tools_deny_override(
        *,
        agent: Any,
        input_kwargs: dict[str, Any],
        decision: Any,
    ) -> Any:
        del agent, decision
        from app.services.ai.runtime.agentscope.tools import enforce_tool_forbidden

        tool = input_kwargs.get("tool")
        tool_call = input_kwargs.get("tool_call")
        tool_name = getattr(tool, "name", None) or getattr(tool_call, "name", None)
        if not tool_name:
            return None
        return await enforce_tool_forbidden(str(tool_name), user_id)

    middlewares: list[Any] = [
        ToolPermissionMiddleware(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_name=agent_name,
            deny_override=_forbidden_tools_deny_override,
        ),
        BashSandboxParentLinkMiddleware(),
    ]
    if conversation_id:
        middlewares.append(
            ModelCallStatsMiddleware(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_name=agent_name,
                trace_id=trace_id,
                physical_window=runtime_info.get("physical_window"),
                history_budget=runtime_info.get("history_budget"),
                overhead_reservation=runtime_info.get("overhead_reservation_tokens"),
                completion_reserve=runtime_info.get("completion_reserve_tokens"),
                request_input_budget=runtime_info.get("request_input_budget"),
                prompt_overhead_reservation=runtime_info.get(
                    "prompt_overhead_reservation_tokens"
                ),
                prompt_layout_mode=runtime_info.get("prompt_layout_mode"),
            )
        )
    return middlewares


async def load_context_config() -> Any:
    """Build AgentScope ContextConfig from platform settings."""
    from agentscope.agent import ContextConfig
    from app.services.config_service import ConfigService

    configs = await ConfigService.get_many({
        "agentscope_context_trigger_ratio": "0.8",
        "agentscope_context_reserve_ratio": "0.1",
        "agentscope_tool_result_limit": "2000",
    })

    def _float(val: Any, default: float) -> float:
        try:
            return float(val) if val not in (None, "") else default
        except (TypeError, ValueError):
            return default

    def _int(val: Any, default: int) -> int:
        try:
            return int(val) if val not in (None, "") else default
        except (TypeError, ValueError):
            return default

    trigger_ratio = _float(configs.get("agentscope_context_trigger_ratio"), 0.8)
    reserve_ratio = _float(configs.get("agentscope_context_reserve_ratio"), 0.1)
    tool_result_limit = _int(configs.get("agentscope_tool_result_limit"), 2000)

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
