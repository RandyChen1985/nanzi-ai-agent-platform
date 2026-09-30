from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from agentscope.message import Base64Source, DataBlock, Msg, TextBlock, URLSource

from app.services.ai.runtime.agentscope.compat import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from app.services.ai.observability.generation_span import traced_generation
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage


def _runtime_text(message: RuntimeMessage) -> str:
    parts = [block.text or "" for block in message.content if block.type == "text"]
    return "\n".join(part for part in parts if part).strip()


def _runtime_blocks_to_agentscope(message: RuntimeMessage) -> list[Any]:
    blocks: list[Any] = []
    for block in message.content:
        if block.type == "text":
            blocks.append(TextBlock(text=block.text or ""))
            continue
        if block.type != "image" or not block.data:
            continue
        mime_type = block.mime_type or "image/png"
        if block.data.startswith("http://") or block.data.startswith("https://"):
            blocks.append(DataBlock(source=URLSource(url=block.data, media_type=mime_type)))
        else:
            data = block.data.split(",", 1)[1] if block.data.startswith("data:") else block.data
            blocks.append(DataBlock(source=Base64Source(data=data, media_type=mime_type)))
    return blocks or [TextBlock(text="")]


def to_agentscope_messages(messages: list[RuntimeMessage]) -> list[Msg]:
    converted: list[Msg] = []
    for message in messages:
        if message.role == "tool":
            text = _runtime_text(message)
            name = message.tool_name or "tool"
            converted.append(
                Msg(
                    name="tool",
                    role="user",
                    content=[TextBlock(text=f"Tool result from {name}: {text}")],
                )
            )
            continue
        converted.append(
            Msg(
                name=message.role,
                role=message.role,
                content=_runtime_blocks_to_agentscope(message),
                metadata=message.metadata,
            )
        )
    return converted


def compat_to_runtime_messages(messages: list[BaseMessage] | str) -> list[RuntimeMessage]:
    if isinstance(messages, str):
        return [
            RuntimeMessage(
                role="user",
                content=[RuntimeContentBlock(type="text", text=messages)],
            )
        ]
    converted: list[RuntimeMessage] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            role = "system"
        elif isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        elif isinstance(message, ToolMessage):
            role = "tool"
        else:
            role = "user"
        converted.append(
            RuntimeMessage(
                role=role,
                content=_compat_content_blocks(getattr(message, "content", "")),
                tool_call_id=getattr(message, "tool_call_id", None),
            )
        )
    return converted


def _compat_content_blocks(content: Any) -> list[RuntimeContentBlock]:
    if isinstance(content, list):
        blocks: list[RuntimeContentBlock] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url":
                image_url = item.get("image_url") or {}
                blocks.append(
                    RuntimeContentBlock(
                        type="image",
                        mime_type="image/png",
                        data=str(image_url.get("url") or ""),
                    )
                )
            elif isinstance(item, dict) and item.get("type") == "text":
                blocks.append(RuntimeContentBlock(type="text", text=str(item.get("text") or "")))
        return blocks or [RuntimeContentBlock(type="text", text="")]
    return [RuntimeContentBlock(type="text", text="" if content is None else str(content))]


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    get_text_content = _safe_getattr(response, "get_text_content")
    if get_text_content:
        text = get_text_content()
        return text or ""
    content = _safe_getattr(response, "content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content)
    if content is None:
        return str(response)
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "".join(parts)


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except (AttributeError, KeyError):
        return default


def _is_structured_output_error(exc: BaseException) -> bool:
    """判断是否为 AgentScope 的结构化输出失败（``StructuredOutputError``）。

    AgentScope 只在内部集齐 forced / auto / no_think / none 四种降级策略之后
    才抛出该异常，所以它是**确定性失败**（模型确实产不出合法结构），与认证
    失败、限流、超时等基础设施类错误性质不同：前者换提示词或换模型才有意义，
    后者重试无用、应当告警。两者此前都被归入 ``"error"``，调用方无从区分。

    延迟导入以兼容不含该异常的旧版 AgentScope。
    """
    try:
        from agentscope.exception import StructuredOutputError
    except ImportError:  # pragma: no cover - 旧版 AgentScope 无此异常
        return False
    return isinstance(exc, StructuredOutputError)


class AgentScopeChatClient:
    def __init__(
        self,
        native_model: Any,
        *,
        model_name: str | None = None,
        source: str | None = None,
    ):
        self.native_model = native_model
        # 供 Langfuse span 记录模型名与业务来源（由 chat_client_from_handle 从 handle 上取）。
        self.model_name = model_name
        self.source = source
        self.last_structured_output_status = "unknown"

    async def generate_structured_dict(
        self,
        messages: list[RuntimeMessage],
        structured_model: Any,
    ) -> dict[str, Any] | None:
        async with traced_generation(
            model=self.model_name,
            streaming=False,
            source=self.source,
            input_messages=messages,
        ) as span:
            try:
                result = await self._generate_structured_dict_impl(
                    messages, structured_model
                )
            finally:
                # 本方法是 fail-open 的：失败不抛异常，只能靠状态属性表达。
                span.record_status(self.last_structured_output_status)
            if result is not None:
                span.record_output(json.dumps(result, ensure_ascii=False))
            return result

    async def _generate_structured_dict_impl(
        self,
        messages: list[RuntimeMessage],
        structured_model: Any,
    ) -> dict[str, Any] | None:
        """Try AgentScope structured output and expose the failure mode to callers."""
        native = self.native_model
        generate_structured_output = getattr(native, "generate_structured_output", None)
        if not callable(generate_structured_output):
            self.last_structured_output_status = "unsupported"
            return None
        try:
            response = await generate_structured_output(
                messages=to_agentscope_messages(messages),
                structured_model=structured_model,
            )
            content = _safe_getattr(response, "content", None)
            if isinstance(content, dict) and content:
                self.last_structured_output_status = "success"
                return content
            model_dump = getattr(content, "model_dump", None)
            if callable(model_dump):
                dumped = model_dump()
                if isinstance(dumped, dict) and dumped:
                    self.last_structured_output_status = "success"
                    return dumped
            if isinstance(content, str):
                text = content.strip()
                if text.startswith("```") and text.endswith("```"):
                    text = text[3:-3].strip()
                    if text.startswith("json"):
                        text = text[4:].strip()
                try:
                    parsed = json.loads(text)
                except (TypeError, json.JSONDecodeError):
                    self.last_structured_output_status = "invalid"
                    return None
                if isinstance(parsed, dict) and parsed:
                    self.last_structured_output_status = "success"
                    return parsed
            self.last_structured_output_status = "invalid"
        except Exception as exc:
            # 保持 fail-open：仍然返回 None、不向上抛。这里只细化状态分类，
            # 调用方的控制流不受影响；若将来要改为向上抛出，需先评估两个调用
            # 点对降级行为的依赖（它们都依赖 fail-open 继续后续流程）。
            if _is_structured_output_error(exc):
                self.last_structured_output_status = "structured_output_error"
            else:
                self.last_structured_output_status = "error"
            return None
        return None

    async def generate_text(self, messages: list[RuntimeMessage], **kwargs: Any) -> str:
        async with traced_generation(
            model=self.model_name,
            streaming=False,
            source=self.source,
            input_messages=messages,
        ) as span:
            result = self.native_model(to_agentscope_messages(messages), **kwargs)
            if inspect.isawaitable(result):
                result = await result

            if _safe_getattr(result, "__aiter__"):
                # AgentScope 基类 ChatModelBase.__call__（_stream/累积器）保证流式返回
                # 形如“增量块(is_last=False) + 完整终帧(is_last=True)”，末帧 content 是
                # 整段累积后的完整正文。因此前面这些增量块逐段累加，遇 is_last=True 时
                # 以完整正文覆盖已累积内容，避免“增量汇总 + 完整末帧”的重复拼接。
                final_text = ""
                async for chunk in result:  # type: ignore[union-attr]
                    text = _response_text(chunk)
                    if getattr(chunk, "is_last", False):
                        final_text = text or final_text
                    else:
                        final_text += text
                span.record_output(final_text)
                return final_text

            if isinstance(result, AsyncIterator):
                final_text = ""
                async for chunk in result:
                    final_text += _response_text(chunk)
                span.record_output(final_text)
                return final_text

            text = _response_text(result)
            span.record_output(text)
            return text

    async def generate_message(
        self,
        messages: list[RuntimeMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        async with traced_generation(
            model=self.model_name,
            streaming=False,
            source=self.source,
            input_messages=messages,
        ) as span:
            result = self.native_model(to_agentscope_messages(messages), tools=tools, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            if _safe_getattr(result, "__aiter__"):
                # AgentScope 基类 __call__ 流式返回“增量块(is_last=False) + 完整终帧
                # (is_last=True)”，终帧 content 是整段累积后的完整正文、tool_calls 也
                # 只在该终帧完整给出。这里直接消费 result（而不是 stream_messages，
                # 以免对新会话重复发请求），逐块累加文本，遇 is_last=True 时以终帧的
                # 完整 AIMessage 覆盖累加的增量内容。
                content_parts: list[str] = []
                final_message: AIMessage | None = None
                async for chunk in result:  # type: ignore[union-attr]
                    message = _chat_response_to_message(chunk)
                    if getattr(chunk, "is_last", False):
                        final_message = message
                    else:
                        content_parts.append(message.content or "")
                if final_message is None:
                    resolved = AIMessage(content="".join(content_parts))
                elif not final_message.content and content_parts:
                    # 终帧 content 已是完整文本；仅当其为空（例如纯工具调用终帧）时回退到增量累积
                    resolved = replace(
                        final_message,
                        content="".join(content_parts),
                    )
                else:
                    resolved = final_message
            else:
                resolved = _chat_response_to_message(result)

            span.record_output(resolved)
            return resolved

    async def stream_messages(
        self,
        messages: list[RuntimeMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AIMessage]:
        async with traced_generation(
            model=self.model_name,
            streaming=True,
            source=self.source,
            input_messages=messages,
        ) as span:
            result = self.native_model(to_agentscope_messages(messages), tools=tools, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            if _safe_getattr(result, "__aiter__"):
                async for chunk in result:
                    # 记录原始 chunk：is_last 终帧标记只在原始 chunk 上，
                    # 转成 AIMessage 后会丢失，导致文本与用量无法正确累积。
                    span.record_output(chunk)
                    yield _chat_response_to_message(chunk)
                return
            span.record_output(result)
            yield _chat_response_to_message(result)


def chat_client_from_handle(llm_handle: Any) -> AgentScopeChatClient:
    native_model = getattr(llm_handle, "native_model", llm_handle)
    return AgentScopeChatClient(
        native_model,
        model_name=(
            getattr(llm_handle, "model_name", None)
            or getattr(native_model, "model_name", None)
        ),
        source=getattr(llm_handle, "source", None),
    )


def _chat_response_to_message(response: Any) -> AIMessage:
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in _safe_getattr(response, "content", []) or []:
        block_type = _safe_getattr(block, "type")
        if block_type == "text":
            content_parts.append(str(_safe_getattr(block, "text", "")))
        elif block_type == "tool_call":
            raw_input = _safe_getattr(block, "input", "") or "{}"
            try:
                args = json.loads(raw_input)
            except Exception:
                args = {"input": raw_input}
            tool_calls.append(
                {
                    "id": _safe_getattr(block, "id", ""),
                    "name": _safe_getattr(block, "name", ""),
                    "args": args,
                }
            )
    usage = _safe_getattr(response, "usage")
    usage_metadata = None
    if usage:
        input_tokens = int(_safe_getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(_safe_getattr(usage, "output_tokens", 0) or 0)
        usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    return AIMessage(
        content="".join(content_parts),
        tool_calls=tool_calls,
        usage_metadata=usage_metadata,
    )


def legacy_tools_to_openai_schemas(tools: list[Any]) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for tool in tools or []:
        name = getattr(tool, "name", None)
        if not name:
            continue
        mcp_input_schema = getattr(tool, "mcp_input_schema", None)
        if isinstance(mcp_input_schema, dict):
            parameters = mcp_input_schema
        else:
            args_schema = getattr(tool, "args_schema", None)
            if args_schema is not None and hasattr(args_schema, "model_json_schema"):
                parameters = args_schema.model_json_schema()
            else:
                parameters = (
                    getattr(tool, "input_schema", None)
                    or getattr(tool, "parameters_schema", None)
                    or {"type": "object", "properties": {}}
                )
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": getattr(tool, "description", "") or "",
                    "parameters": parameters,
                },
            }
        )
    return schemas
