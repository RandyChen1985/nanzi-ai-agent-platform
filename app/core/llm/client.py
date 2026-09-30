import inspect
import logging
from dataclasses import dataclass
from typing import Optional, Any

from app.core.config import settings
from app.services.ai.runtime.agentscope.models import (
    AgentScopeModelConfig,
    create_openai_chat_model,
)
from app.services.ai.runtime.agentscope.request_timeout import (
    LLM_REQUEST_READ_TIMEOUT_KEY,
    parse_llm_request_read_timeout,
)
from app.utils.model_credentials import decrypt_model_api_key
from app.core.context import get_debug_option
from app.services.ai.reasoning import UNSET, resolve_reasoning_settings
from app.services.ai.temperature import session_temperature_override

logger = logging.getLogger(__name__)


@dataclass
class AgentScopeLLMHandle:
    native_model: Any
    model_name: str
    temperature: float
    streaming: bool
    api_base_url: str | None = None
    tool_schemas: list[dict[str, Any]] | None = None
    # 业务来源标签（如 "chatbi.sql"），仅用于 Langfuse 排障与聚合，不参与模型调用。
    source: str | None = None

    @property
    def model(self) -> str:
        return self.model_name

    def bind_tools(self, tools: list[Any]) -> "AgentScopeLLMHandle":
        from app.services.ai.runtime.agentscope.chat import legacy_tools_to_openai_schemas

        return AgentScopeLLMHandle(
            native_model=self.native_model,
            model_name=self.model_name,
            temperature=self.temperature,
            streaming=self.streaming,
            api_base_url=self.api_base_url,
            tool_schemas=legacy_tools_to_openai_schemas(tools),
            source=self.source,
        )

    async def ainvoke(self, messages: Any):
        from app.services.ai.runtime.agentscope.chat import (
            chat_client_from_handle,
            compat_to_runtime_messages,
        )

        return await chat_client_from_handle(self).generate_message(
            compat_to_runtime_messages(messages),
            tools=self.tool_schemas,
        )

    async def astream(self, messages: Any):
        from app.services.ai.runtime.agentscope.chat import (
            chat_client_from_handle,
            compat_to_runtime_messages,
        )

        async for chunk in chat_client_from_handle(self).stream_messages(
            compat_to_runtime_messages(messages),
            tools=self.tool_schemas,
        ):
            yield chunk


class ConfigServiceProxy:
    @staticmethod
    async def get(key: str):
        from app.services.config_service import ConfigService

        return await ConfigService.get(key)


async def _lookup_ai_model_record(model: str):
    try:
        from app.services.ai.model_registry import lookup_registered_model

        return await lookup_registered_model(model)
    except Exception as exc:
        from app.services.ai.model_registry import ModelRegistryError

        if isinstance(exc, ModelRegistryError):
            raise
        logger.warning("Model registry lookup failed in get_llm_async: %s", exc)
        return None


def _parse_temperature(value: Any, default: float = 0.7) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class LLMFactory:
    """
    Factory for creating AgentScope chat model handles.
    Centralizes LLM configuration and allows provider-compatible overrides.
    """

    @staticmethod
    def get_chat_model(
        streaming: bool = False,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        temperature: float | None = None,
        context_size: int | None = None,
        max_output_tokens: int | None = None,
        thinking_enable: bool = False,
        thinking_capable: bool = False,
        reasoning_effort: str | None = None,
        read_timeout: float | None = None,
        source: str | None = None,
    ) -> AgentScopeLLMHandle:
        final_api_key = api_key or (settings.LLM_API_KEY if settings.LLM_API_KEY else None)
        final_base_url = base_url or (settings.LLM_BASE_URL if settings.LLM_BASE_URL else None)
        final_model = model or (settings.LLM_MODEL_NAME if settings.LLM_MODEL_NAME else "default-model")
        final_temp = (
            temperature
            if temperature is not None
            else (
                settings.LLM_TEMPERATURE
                if settings.LLM_TEMPERATURE is not None
                else 0.7
            )
        )

        masked_key = final_api_key[:8] + "***" if final_api_key else "None"
        logger.info(
            "Creating AgentScope OpenAI-compatible model: model=%s base_url=%s key=%s",
            final_model,
            final_base_url,
            masked_key,
        )

        native_model = create_openai_chat_model(
            AgentScopeModelConfig(
                api_key=final_api_key,
                base_url=final_base_url,
                model=final_model,
                provider=provider,
                temperature=float(final_temp),
                streaming=streaming,
                context_size=context_size,
                max_output_tokens=max_output_tokens,
                thinking_enable=thinking_enable,
                thinking_capable=thinking_capable,
                reasoning_effort=reasoning_effort,
                read_timeout=read_timeout,
            )
        )

        return AgentScopeLLMHandle(
            native_model=native_model,
            model_name=final_model,
            temperature=float(final_temp),
            streaming=streaming,
            api_base_url=final_base_url,
            source=source,
        )


def get_llm(streaming: bool = False, **kwargs) -> AgentScopeLLMHandle:
    return LLMFactory.get_chat_model(streaming=streaming, **kwargs)


async def get_llm_async(streaming: bool = False, **kwargs) -> Optional[AgentScopeLLMHandle]:
    """
    Asynchronously create an AgentScope LLM handle.

    Priority:
    1. kwargs overrides
    2. ai_models table lookup if model name matches
    3. system_configs / environment fallback
    """
    ignore_session_reasoning_overrides = bool(
        kwargs.pop("ignore_session_reasoning_overrides", False)
    )
    # 内部链路（意图识别等）用这个开关声明「不要跟随用户的采样温度」。
    ignore_session_temperature = bool(
        kwargs.pop("ignore_session_temperature", False)
    )
    db_model_name = await ConfigServiceProxy.get("llm_model_name")
    model = kwargs.get("model") or db_model_name or settings.LLM_MODEL_NAME or "default-model"

    api_key = kwargs.get("api_key")
    base_url = kwargs.get("base_url")
    context_size = kwargs.get("context_size")
    max_output_tokens = kwargs.get("max_output_tokens")
    provider = kwargs.get("provider")
    thinking_enable = kwargs.get("thinking_enable")
    thinking_capable = kwargs.get("thinking_capable")
    reasoning_effort = kwargs.get("reasoning_effort")

    lookup_result = _lookup_ai_model_record(model)
    ai_model = await lookup_result if inspect.isawaitable(lookup_result) else lookup_result
    if ai_model:
        api_key = api_key or decrypt_model_api_key(getattr(ai_model, "api_key", None))
        base_url = base_url or getattr(ai_model, "api_base_url", None)
        model = getattr(ai_model, "model_id", model)
        provider = provider or getattr(ai_model, "provider", None)
        context_size = (
            context_size
            if context_size is not None
            else getattr(ai_model, "context_size", None)
        )
        max_output_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else getattr(ai_model, "max_output_tokens", None)
        )
        registered_thinking_enable = bool(getattr(ai_model, "thinking_enable", False))
        thinking_capable = registered_thinking_enable
        registered_reasoning_effort = getattr(ai_model, "reasoning_effort", None)
        reasoning_settings = resolve_reasoning_settings(
            thinking_enable=registered_thinking_enable,
            reasoning_effort=registered_reasoning_effort,
            thinking_only=bool(getattr(ai_model, "thinking_only", False)),
            allow_disable_thinking=bool(getattr(ai_model, "allow_disable_thinking", True)),
            supported_reasoning_efforts=getattr(ai_model, "supported_reasoning_efforts", None),
            overrides={
                "thinking_enable": (
                    thinking_enable
                    if thinking_enable is not None
                    else (
                        UNSET
                        if ignore_session_reasoning_overrides
                        else get_debug_option("thinking_enable", UNSET)
                    )
                ),
                "reasoning_effort": (
                    reasoning_effort
                    if reasoning_effort is not None
                    else (
                        UNSET
                        if ignore_session_reasoning_overrides
                        else get_debug_option("reasoning_effort", UNSET)
                    )
                ),
            },
        )
        thinking_enable = reasoning_settings.thinking_enable
        reasoning_effort = reasoning_settings.reasoning_effort

    if not api_key:
        api_key = await ConfigServiceProxy.get("llm_api_key") or settings.LLM_API_KEY
    if not base_url:
        base_url = await ConfigServiceProxy.get("llm_base_url") or settings.LLM_BASE_URL

    db_temp = await ConfigServiceProxy.get("llm_temperature")
    # 显式入参 > 会话级覆盖 > 系统全局：会话覆盖此前在这条通道上被完全忽略，
    # 导致同一个输入框温度开关在不同链路行为不一致。
    requested_temp = kwargs.get("temperature")
    if requested_temp is None and not ignore_session_temperature:
        requested_temp = session_temperature_override()
    if requested_temp is None:
        requested_temp = db_temp
    temperature = _parse_temperature(
        requested_temp,
        default=_parse_temperature(settings.LLM_TEMPERATURE, default=0.7),
    )

    if not api_key:
        logger.error("LLM API Key is missing for model '%s'. Cannot create LLM instance.", model)
        return None

    source = kwargs.get("source")
    read_timeout = kwargs.get("read_timeout")
    if read_timeout is None:
        read_timeout = parse_llm_request_read_timeout(
            await ConfigServiceProxy.get(LLM_REQUEST_READ_TIMEOUT_KEY)
        )

    factory_kwargs = {
        "streaming": streaming,
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "provider": provider,
        "temperature": temperature,
        "read_timeout": read_timeout,
    }
    if provider is None:
        factory_kwargs.pop("provider")
    if context_size is not None:
        factory_kwargs["context_size"] = context_size
    if max_output_tokens is not None:
        factory_kwargs["max_output_tokens"] = max_output_tokens
    if thinking_enable is not None:
        factory_kwargs["thinking_enable"] = thinking_enable
    if thinking_capable is not None:
        factory_kwargs["thinking_capable"] = thinking_capable
    if reasoning_effort is not None:
        factory_kwargs["reasoning_effort"] = reasoning_effort
    if source is not None:
        factory_kwargs["source"] = source

    return LLMFactory.get_chat_model(**factory_kwargs)
