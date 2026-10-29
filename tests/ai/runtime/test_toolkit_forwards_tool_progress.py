"""端到端（无 LLM）：Toolkit 必须把进度片逐片转发，而不是塌成一片。

这一层是 P0-1 的「最后一公里」：`AgentScopeRuntimeTool` 返回生成器之后，
AgentScope 的 `Toolkit.call_tool` 负责把每一片变成流事件（DELTA），
最后由 agent loop 转成 `CustomEvent(mcp_progress)`。若 Toolkit 只取最后一片，
前端就永远收不到进度——所以这里用真实 Toolkit 跑一遍。
"""

from __future__ import annotations

from typing import Any

import pytest
from agentscope.message import ToolCallBlock, ToolResultState
from agentscope.tool import ToolChunk, Toolkit

from app.services.ai.runtime.agentscope.tools import (
    runtime_tool_from_spec,
    runtime_tool_spec_from_legacy_tool,
)

pytestmark = pytest.mark.no_infrastructure


def _progress(progress: float, total: float, message: str) -> ToolChunk:
    return ToolChunk(
        content=[],
        state=ToolResultState.RUNNING,
        is_last=False,
        metadata={"mcp_progress": {"progress": progress, "total": total, "message": message}},
    )


class _StreamingTool:
    name = "mcp_probe"
    description = "streams progress"
    is_read_only = True

    async def ainvoke(self, arguments: dict[str, Any] | None = None):
        async def _gen():
            for index in (1, 2, 3):
                yield _progress(float(index), 3.0, f"step {index}")
            yield "final-answer"

        return _gen()


async def test_toolkit_forwards_each_progress_chunk():
    spec = runtime_tool_spec_from_legacy_tool(_StreamingTool(), source_type="mcp")
    tool = runtime_tool_from_spec(spec)
    toolkit = Toolkit(tools=[tool])

    call = ToolCallBlock(
        type="tool_call",
        id="tc-progress",
        name=tool.name,
        input="{}",
    )
    # Toolkit.call_tool 需要 AgentState；用真实状态对象避免 mock 掉框架行为
    from agentscope.state import AgentState

    produced = [chunk async for chunk in toolkit.call_tool(call, AgentState())]

    progress_events = [
        chunk
        for chunk in produced
        if isinstance(chunk, ToolChunk) and chunk.is_last is False
    ]
    assert len(progress_events) == 3, (
        f"Toolkit 必须逐片转发 3 个进度片，实际：{[type(c).__name__ for c in produced]}"
    )
    assert [
        chunk.metadata["mcp_progress"]["progress"] for chunk in progress_events
    ] == [1.0, 2.0, 3.0]

    # 最后一项是 Toolkit 汇总出的 ToolResponse（含最终结果文本）
    final = produced[-1]
    final_text = "".join(
        getattr(block, "text", "") for block in (getattr(final, "content", None) or [])
    )
    assert "final-answer" in final_text, f"最终结果丢失：{final!r}"
