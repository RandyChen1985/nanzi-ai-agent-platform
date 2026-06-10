from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BaseMessage:
    content: Any


@dataclass
class SystemMessage(BaseMessage):
    pass


@dataclass
class HumanMessage(BaseMessage):
    pass


@dataclass
class AIMessage(BaseMessage):
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage_metadata: dict[str, int] | None = None
    response_metadata: dict[str, Any] = field(default_factory=dict)

    def __add__(self, other: "AIMessage") -> "AIMessage":
        content = f"{self.content or ''}{other.content or ''}"
        tool_calls = [*self.tool_calls, *other.tool_calls]
        usage = other.usage_metadata or self.usage_metadata
        return AIMessage(content=content, tool_calls=tool_calls, usage_metadata=usage)


@dataclass
class ToolMessage(BaseMessage):
    tool_call_id: str

