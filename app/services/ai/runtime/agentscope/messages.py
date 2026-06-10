from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


RuntimeRole = Literal["system", "user", "assistant", "tool"]
RuntimeBlockType = Literal["text", "image"]


@dataclass(frozen=True)
class RuntimeContentBlock:
    type: RuntimeBlockType
    text: str | None = None
    mime_type: str | None = None
    data: str | None = None


@dataclass(frozen=True)
class RuntimeMessage:
    role: RuntimeRole
    content: list[RuntimeContentBlock] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _text_block(content: Any) -> RuntimeContentBlock:
    return RuntimeContentBlock(type="text", text="" if content is None else str(content))


def _attachment_blocks(message: dict[str, Any]) -> list[RuntimeContentBlock]:
    blocks: list[RuntimeContentBlock] = []
    for attachment in message.get("attachments") or []:
        data = attachment.get("data_url") or attachment.get("url") or attachment.get("data")
        if not data:
            continue
        mime_type = attachment.get("mime_type") or attachment.get("content_type")
        if mime_type and mime_type.startswith("image/"):
            blocks.append(RuntimeContentBlock(type="image", mime_type=mime_type, data=str(data)))
    return blocks


def convert_history_to_runtime_messages(history: list[dict[str, Any]]) -> list[RuntimeMessage]:
    messages: list[RuntimeMessage] = []
    for item in history:
        role = item.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            continue

        content = [_text_block(item.get("content"))]
        if role == "user":
            content.extend(_attachment_blocks(item))

        messages.append(
            RuntimeMessage(
                role=role,
                content=content,
                tool_call_id=item.get("tool_call_id"),
                tool_name=item.get("name") or item.get("tool_name"),
                metadata=item.get("metadata") or {},
            )
        )
    return messages

