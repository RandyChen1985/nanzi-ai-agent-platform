import inspect
import logging
from dataclasses import dataclass
from typing import Optional, Any

from app.core.config import settings
from app.services.ai.runtime.agentscope.models import (
    AgentScopeModelConfig,
    create_openai_chat_model,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentScopeLLMHandle:
    native_model: Any
    model_name: str
    temperature: float
    streaming: bool
    api_base_url: str | None = None
    tool_schemas: list[dict[str, Any]] | None = None

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
        from app.core.orm import AsyncSessionLocal
        from app.models.ai_model import AIModel
        from sqlalchemy import or_, select

        async with AsyncSessionLocal() as session:
            stmt = select(AIModel).where(
                AIModel.is_active == True,
                or_(AIModel.model_id == model, AIModel.name == model),
            )
            result = await session.execute(stmt)
            return result.scalars().first()
    except Exception as exc:
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
        temperature: float | None = None,
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
                temperature=float(final_temp),
                streaming=streaming,
            )
        )

        return AgentScopeLLMHandle(
            native_model=native_model,
            model_name=final_model,
            temperature=float(final_temp),
            streaming=streaming,
            api_base_url=final_base_url,
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
    db_model_name = await ConfigServiceProxy.get("llm_model_name")
    model = kwargs.get("model") or db_model_name or settings.LLM_MODEL_NAME or "default-model"

    api_key = kwargs.get("api_key")
    base_url = kwargs.get("base_url")

    lookup_result = _lookup_ai_model_record(model)
    ai_model = await lookup_result if inspect.isawaitable(lookup_result) else lookup_result
    if ai_model:
        api_key = api_key or getattr(ai_model, "api_key", None)
        base_url = base_url or getattr(ai_model, "api_base_url", None)
        model = getattr(ai_model, "model_id", model)

    if not api_key:
        api_key = await ConfigServiceProxy.get("llm_api_key") or settings.LLM_API_KEY
    if not base_url:
        base_url = await ConfigServiceProxy.get("llm_base_url") or settings.LLM_BASE_URL

    db_temp = await ConfigServiceProxy.get("llm_temperature")
    temperature = _parse_temperature(
        kwargs.get("temperature") if kwargs.get("temperature") is not None else db_temp,
        default=_parse_temperature(settings.LLM_TEMPERATURE, default=0.7),
    )

    if not api_key:
        logger.error("LLM API Key is missing for model '%s'. Cannot create LLM instance.", model)
        return None

    return LLMFactory.get_chat_model(
        streaming=streaming,
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temperature,
    )
