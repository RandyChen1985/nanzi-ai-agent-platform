from __future__ import annotations

import json
from typing import Any


class AgentScopeEventAdapter:
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id

    def to_sse_chunk(self, event: Any) -> dict[str, Any] | None:
        event_type = str(getattr(event, "type", ""))

        if event_type == "TEXT_BLOCK_DELTA":
            return {"content": str(getattr(event, "delta", ""))}

        if event_type.startswith("THINKING_BLOCK"):
            return {"type": "thinking", "status": "continuing"}

        if event_type == "TOOL_CALL_START":
            tool_name = getattr(event, "name", "") or getattr(event, "tool_name", "")
            arguments = getattr(event, "arguments", None)
            return {
                "type": "log",
                "id": getattr(event, "id", None) or f"{self.trace_id}_tool_start",
                "title": f"调用工具: {tool_name}",
                "details": json.dumps(arguments or {}, ensure_ascii=False),
                "status": "pending",
                "category": "tool",
            }

        if event_type == "TOOL_CALL_END":
            tool_name = getattr(event, "name", "") or getattr(event, "tool_name", "")
            return {
                "type": "log",
                "id": getattr(event, "id", None) or f"{self.trace_id}_tool_end",
                "title": f"工具完成: {tool_name}",
                "details": str(getattr(event, "result", "")),
                "status": "success",
                "category": "tool",
            }

        return None

