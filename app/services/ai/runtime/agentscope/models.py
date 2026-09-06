from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)


def _is_forced_tool_choice(tool_choice: Any) -> bool:
    mode = getattr(tool_choice, "mode", None)
    if mode is None and isinstance(tool_choice, str):
        mode = tool_choice
    return tool_choice is not None and mode not in {"auto", "none"}


def _is_thinking_tool_choice_error(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None and status_code != 400:
        return False

    body = getattr(error, "body", None)
    error_text = f"{error} {body or ''}".lower()
    return (
        "tool_choice" in error_text
        and any(
            marker in error_text
            for marker in ("thinking", "thirking", "reasoning")
        )
        and any(marker in error_text for marker in ("required", "object"))
    )


@dataclass(frozen=True)
class AgentScopeModelConfig:
    api_key: str | None
    base_url: str | None
    model: str
    temperature: float = 0.0
    streaming: bool = True
    max_retries: int = 3
    context_size: int | None = None
    max_output_tokens: int | None = None
    provider: str | None = None
    thinking_enable: bool = False
    thinking_capable: bool = False
    reasoning_effort: str | None = None


def _chat_template_kwargs(config: AgentScopeModelConfig) -> dict[str, Any] | None:
    """Build legacy template controls for custom OpenAI-compatible gateways.

    Thinking-capable models that default to reasoning still need an explicit
    ``false`` so omitting the field does not leave thinking on.
    """
    if not config.thinking_enable and not config.thinking_capable:
        return None
    kwargs: dict[str, Any] = {
        "thinking": bool(config.thinking_enable),
        "enable_thinking": bool(config.thinking_enable),
    }
    if config.thinking_enable and config.reasoning_effort is not None:
        kwargs["reasoning_effort"] = config.reasoning_effort
    return kwargs


_THINKING_TYPE_PROVIDERS = frozenset({"kimi", "zhipu", "volcengine", "volces"})
_ENABLE_THINKING_PROVIDERS = frozenset({"dashscope", "siliconflow"})


def _normalized_provider(config: AgentScopeModelConfig) -> str:
    return str(config.provider or "").strip().lower()


def _normalized_model(config: AgentScopeModelConfig) -> str:
    return str(config.model or "").strip().lower()


def _is_deepseek_v4(config: AgentScopeModelConfig) -> bool:
    return _normalized_provider(config) == "deepseek" and _normalized_model(config) in {
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }


def _thinking_protocol(config: AgentScopeModelConfig) -> str | None:
    """Resolve the request-body protocol without exposing it to users.

    The registry owns whether a model is thinking-capable. For known providers
    we only emit their documented extension when that capability is enabled;
    unknown/custom gateways retain the historical template compatibility path.
    """
    provider = _normalized_provider(config)
    if _is_deepseek_v4(config):
        return "thinking_type"
    if provider in _THINKING_TYPE_PROVIDERS:
        return "thinking_type" if config.thinking_capable else None
    if provider in _ENABLE_THINKING_PROVIDERS:
        return "enable_thinking" if config.thinking_capable else None
    if provider == "ollama":
        return "ollama_think" if config.thinking_capable else None
    if provider in {"", "other"}:
        return "legacy"
    return None


def _request_extra_body(config: AgentScopeModelConfig) -> dict[str, Any] | None:
    protocol = _thinking_protocol(config)
    if protocol == "thinking_type":
        return {
            "thinking": {
                "type": "enabled" if config.thinking_enable else "disabled",
            },
        }
    if protocol == "enable_thinking":
        return {"enable_thinking": bool(config.thinking_enable)}
    if protocol == "ollama_think":
        return {"think": bool(config.thinking_enable)}
    if protocol == "legacy":
        template_kwargs = _chat_template_kwargs(config)
        if template_kwargs:
            return {"chat_template_kwargs": template_kwargs}
    return None


def _disabled_request_extra_body(
    protocol: str | None,
    legacy_chat_template_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Build the protocol-specific body used by a thinking-off retry."""
    if protocol == "thinking_type":
        return {"thinking": {"type": "disabled"}}
    if protocol == "enable_thinking":
        return {"enable_thinking": False}
    if protocol == "ollama_think":
        return {"think": False}
    if protocol == "legacy":
        return {"chat_template_kwargs": dict(legacy_chat_template_kwargs)}
    return {}


def create_openai_chat_model(config: AgentScopeModelConfig):
    if not config.api_key:
        raise ValueError(f"LLM API Key is missing for model '{config.model}'")

    try:
        from agentscope.credential import OpenAICredential
        from agentscope.model import OpenAIChatModel
    except Exception as exc:
        raise RuntimeError(
            "AgentScope OpenAI chat model dependencies are not available"
        ) from exc

    thinking_protocol = _thinking_protocol(config)
    chat_template_kwargs = (
        _chat_template_kwargs(config)
        if thinking_protocol == "legacy"
        else None
    )
    request_extra_body = _request_extra_body(config)
    is_deepseek_v4 = _is_deepseek_v4(config)

    class PlatformOpenAIChatModel(OpenAIChatModel):
        """Keep native AgentScope parameters and inject request controls."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._chat_template_kwargs = dict(chat_template_kwargs or {})
            self._request_extra_body = copy.deepcopy(request_extra_body or {})
            self._thinking_protocol = thinking_protocol
            self._is_deepseek_v4 = is_deepseek_v4
            super().__init__(*args, **kwargs)

        async def _call_api_once(self, *args: Any, **kwargs: Any) -> Any:
            request_kwargs = dict(kwargs)
            if (
                self._is_deepseek_v4
                and self.parameters.thinking_enable
                and _is_forced_tool_choice(request_kwargs.get("tool_choice"))
            ):
                request_kwargs.pop("tool_choice", None)
            if self._request_extra_body:
                extra_body = dict(request_kwargs.get("extra_body") or {})
                for key, value in self._request_extra_body.items():
                    if key == "chat_template_kwargs":
                        extra_body.setdefault(key, copy.deepcopy(value))
                    else:
                        extra_body[key] = copy.deepcopy(value)
                request_kwargs["extra_body"] = extra_body
            return await super()._call_api(*args, **request_kwargs)

        async def _call_api(self, *args: Any, **kwargs: Any) -> Any:
            import openai

            try:
                return await self._call_api_once(*args, **kwargs)
            except openai.BadRequestError as exc:
                tool_choice = kwargs.get("tool_choice")
                if not (
                    self.parameters.thinking_enable
                    and _is_forced_tool_choice(tool_choice)
                    and _is_thinking_tool_choice_error(exc)
                ):
                    raise

                logger.warning(
                    "[AgentScope] Provider rejected forced tool_choice in "
                    "thinking mode; retrying model=%s with thinking disabled "
                    "for this request",
                    self.model,
                )
                fallback = copy.copy(self)
                fallback.parameters = self.parameters.model_copy(
                    update={
                        "thinking_enable": False,
                        "reasoning_effort": None,
                    },
                )
                fallback._chat_template_kwargs = dict(self._chat_template_kwargs)
                fallback._chat_template_kwargs.update(
                    {
                        "thinking": False,
                        "enable_thinking": False,
                    },
                )
                fallback._chat_template_kwargs.pop("reasoning_effort", None)
                fallback._request_extra_body = _disabled_request_extra_body(
                    fallback._thinking_protocol,
                    fallback._chat_template_kwargs,
                )
                fallback_kwargs = dict(kwargs)
                fallback_extra_body = dict(fallback_kwargs.get("extra_body") or {})
                for key in ("thinking", "enable_thinking", "think"):
                    fallback_extra_body.pop(key, None)
                if fallback._thinking_protocol == "legacy":
                    fallback_chat_template_kwargs = dict(
                        fallback_extra_body.get("chat_template_kwargs") or {},
                    )
                    fallback_chat_template_kwargs.update(
                        fallback._chat_template_kwargs,
                    )
                    fallback_extra_body["chat_template_kwargs"] = (
                        fallback_chat_template_kwargs
                    )
                else:
                    fallback_extra_body.pop("chat_template_kwargs", None)
                    fallback_extra_body.update(
                        copy.deepcopy(fallback._request_extra_body),
                    )
                fallback_kwargs["extra_body"] = fallback_extra_body
                from agentscope.tool import ToolChoice

                fallback_kwargs["tool_choice"] = ToolChoice(mode="auto")
                return await fallback._call_api_once(*args, **fallback_kwargs)

    parameters = OpenAIChatModel.Parameters(
        temperature=config.temperature,
        max_tokens=config.max_output_tokens,
        thinking_enable=config.thinking_enable,
        reasoning_effort=config.reasoning_effort,
    )
    model_kwargs = {
        "credential": OpenAICredential(
            api_key=config.api_key,
            base_url=config.base_url,
        ),
        "model": config.model,
        "stream": config.streaming,
        "parameters": parameters,
        "max_retries": config.max_retries,
        # AgentScope owns the retry budget; SDK retries would multiply it.
        "client_kwargs": {"max_retries": 0},
    }
    if config.provider == "azure":
        from app.utils.model_providers import azure_openai_request_config

        azure_base_url, api_version = azure_openai_request_config(
            config.base_url,
            config.model,
        )
        model_kwargs["credential"] = OpenAICredential(
            api_key=config.api_key,
            base_url=azure_base_url,
        )
        model_kwargs["client_kwargs"].update({
            "default_headers": {"api-key": config.api_key},
            "default_query": {"api-version": api_version},
        })
    if config.context_size is not None:
        model_kwargs["context_size"] = config.context_size

    return PlatformOpenAIChatModel(
        **model_kwargs,
    )
