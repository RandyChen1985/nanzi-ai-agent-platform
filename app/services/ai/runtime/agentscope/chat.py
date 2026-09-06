from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from typing import Any

from agentscope.message import Base64Source, DataBlock, Msg, TextBlock, URLSource

from app.services.ai.runtime.agentscope.compat import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
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


class AgentScopeChatClient:
    def __init__(self, native_model: Any):
        self.native_model = native_model
        self.last_structured_output_status = "unknown"

    async def generate_structured_dict(
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
        except Exception:
            self.last_structured_output_status = "error"
            return None
        return None

    async def generate_text(self, messages: list[RuntimeMessage], **kwargs: Any) -> str:
        result = self.native_model(to_agentscope_messages(messages), **kwargs)
        if inspect.isawaitable(result):
            result = await result

        if _safe_getattr(result, "__aiter__"):
            # 与 stream_messages / generate_message 一致：逐块累加增量正文。
            # 旧逻辑仅在遇到 is_last 标记时整体覆盖、且非首块会被丢弃，遇“增量 delta +
            # 末端仅作哨兵”的流式返回会截断/错乱（审计发现）。
            final_text = ""
            async for chunk in result:  # type: ignore[union-attr]
                final_text += _response_text(chunk)
            return final_text

        if isinstance(result, AsyncIterator):
            final_text = ""
            async for chunk in result:
                final_text += _response_text(chunk)
            return final_text

        return _response_text(result)

    async def generate_message(
        self,
        messages: list[RuntimeMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        result = self.native_model(to_agentscope_messages(messages), tools=tools, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if _safe_getattr(result, "__aiter__"):
            final_message = AIMessage(content="")
            async for chunk in self.stream_messages(messages, tools=tools, **kwargs):
                final_message += chunk
            return final_message
        return _chat_response_to_message(result)

    async def stream_messages(
        self,
        messages: list[RuntimeMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AIMessage]:
        result = self.native_model(to_agentscope_messages(messages), tools=tools, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if _safe_getattr(result, "__aiter__"):
            async for chunk in result:
                yield _chat_response_to_message(chunk)
            return
        yield _chat_response_to_message(result)


def chat_client_from_handle(llm_handle: Any) -> AgentScopeChatClient:
    native_model = getattr(llm_handle, "native_model", llm_handle)
    return AgentScopeChatClient(native_model)


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
