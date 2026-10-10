from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.services.ai.runtime.agentscope.request_timeout import (
    build_llm_request_timeout,
)


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
    #: HTTP read timeout（chunk 间隔）秒数；None 表示使用平台默认值。
    read_timeout: float | None = None


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


_MAX_FAILURE_DETAIL_CHARS = 1000


def _extract_failure_detail(
    exc: BaseException,
) -> tuple[Any, str | None, str | None]:
    """提取上游失败的可排查信息：HTTP 状态码 / 错误码 / 响应摘要。"""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)

    code: str | None = None
    upstream: str | None = None
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            raw_code = error.get("code") or error.get("type")
            if raw_code:
                code = str(raw_code)
            message = error.get("message")
            if message:
                upstream = str(message)
        elif body.get("message"):
            upstream = str(body["message"])
        if upstream is None:
            try:
                upstream = json.dumps(body, ensure_ascii=False, default=str)
            except Exception:
                upstream = None

    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)

    if upstream is None:
        response = getattr(exc, "response", None)
        text = getattr(response, "text", None)
        if text:
            upstream = str(text)

    if upstream is None:
        upstream = str(exc) or None

    if upstream is not None:
        upstream = upstream[:_MAX_FAILURE_DETAIL_CHARS]

    return status, code, upstream


def _describe_request(request_kwargs: dict[str, Any]) -> str:
    """描述请求形状（不含消息正文与凭据），用于把失败定位到具体调用。"""
    messages = request_kwargs.get("messages") or []
    tools = request_kwargs.get("tools") or []
    tool_choice = request_kwargs.get("tool_choice")
    mode = getattr(tool_choice, "mode", None)
    if mode is None and isinstance(tool_choice, str):
        mode = tool_choice

    parts = [f"messages={len(messages)}", f"tools={len(tools)}"]
    if mode:
        parts.append(f"tool_choice={mode}")
    extra_body = request_kwargs.get("extra_body")
    if isinstance(extra_body, dict) and extra_body:
        parts.append("extra_body=" + ",".join(sorted(map(str, extra_body))))
    return " ".join(parts)


def _log_model_call_failure(
    model: Any,
    exc: BaseException,
    *,
    phase: str,
    request_kwargs: dict[str, Any] | None = None,
) -> None:
    """记录一次模型调用失败的上游详情。

    主模型失败后 AgentScope 会静默降级到 fallback，异常被丢弃，日志里只剩
    "exhausted all N attempt(s)"。这里在平台自己的调用边界补上留痕。

    只记录，绝不改变控制流：记录过程本身的任何异常都在此处兜住，最差退化成
    一条最小日志。
    """
    model_name = str(getattr(model, "model", None) or "unknown")
    try:
        base_url = str(
            getattr(getattr(model, "credential", None), "base_url", None)
            or "unknown"
        )
        status, code, upstream = _extract_failure_detail(exc)
        logger.warning(
            "[AgentScope] Model call failed: model=%s base_url=%s phase=%s "
            "status=%s error_type=%s error_code=%s request=%s upstream=%s",
            model_name,
            base_url,
            phase,
            status if status is not None else "-",
            type(exc).__name__,
            code or "-",
            _describe_request(request_kwargs or {}),
            upstream or "-",
        )
    except BaseException:
        try:
            logger.warning(
                "[AgentScope] Model call failed: model=%s phase=%s error_type=%s",
                model_name,
                phase,
                type(exc).__name__,
            )
        except BaseException:
            pass


async def _log_stream_failures(stream: Any, model: Any) -> Any:
    """透传流式响应，只在中途失败时补一条失败日志。"""
    try:
        async for item in stream:
            yield item
    except Exception as exc:
        _log_model_call_failure(model, exc, phase="stream")
        raise


def _wrap_stream_failures(result: Any, model: Any) -> Any:
    if not hasattr(result, "__aiter__"):
        return result
    return _log_stream_failures(result, model)


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
                result = await self._call_api_once(*args, **kwargs)
            except openai.BadRequestError as exc:
                tool_choice = kwargs.get("tool_choice")
                if not (
                    self.parameters.thinking_enable
                    and _is_forced_tool_choice(tool_choice)
                    and _is_thinking_tool_choice_error(exc)
                ):
                    _log_model_call_failure(
                        self,
                        exc,
                        phase="request",
                        request_kwargs=kwargs,
                    )
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
                try:
                    result = await fallback._call_api_once(*args, **fallback_kwargs)
                except Exception as retry_exc:
                    _log_model_call_failure(
                        self,
                        retry_exc,
                        phase="thinking-off-retry",
                        request_kwargs=fallback_kwargs,
                    )
                    raise
            except Exception as exc:
                _log_model_call_failure(
                    self,
                    exc,
                    phase="request",
                    request_kwargs=kwargs,
                )
                raise

            return _wrap_stream_failures(result, self)

    parameters = OpenAIChatModel.Parameters(
        temperature=config.temperature,
        max_tokens=config.max_output_tokens,
        thinking_enable=config.thinking_enable,
        reasoning_effort=config.reasoning_effort,
    )
    # AgentScope 拥有重试预算，SDK 自身重试会与之相乘，因此关闭。
    # timeout 必须显式给全四项：httpx 的 Timeout 在「无默认值且未给全」时会抛
    # ValueError，而 SDK 默认的 600s read timeout 会让业务层重试来不及执行。
    client_kwargs: dict[str, Any] = {"max_retries": 0}
    request_timeout = build_llm_request_timeout(config.read_timeout)
    if request_timeout is not None:
        client_kwargs["timeout"] = request_timeout

    model_kwargs = {
        "credential": OpenAICredential(
            api_key=config.api_key,
            base_url=config.base_url,
        ),
        "model": config.model,
        "stream": config.streaming,
        "parameters": parameters,
        "max_retries": config.max_retries,
        "client_kwargs": client_kwargs,
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
