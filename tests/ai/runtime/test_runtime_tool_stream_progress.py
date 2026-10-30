"""运行时工具层：异步生成器工具必须能流式透传进度片。

契约：``spec.callable`` 返回异步生成器时，``AgentScopeRuntimeTool.__call__``
返回异步生成器 —— 中间分片原样上抛（agent 会转成 ``CustomEvent``），最后一个
分片是由**业务结果**包装出的最终 ``ToolChunk``，证据与审计只记一次。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from agentscope.message import ToolResultState
from agentscope.tool import ToolChunk

from app.services.ai.runtime.agentscope import tools as runtime_tools
from app.services.ai.runtime.agentscope.tools import (
    runtime_tool_from_spec,
    runtime_tool_spec_from_legacy_tool,
)

pytestmark = pytest.mark.no_infrastructure


def _progress_chunk(progress: float, total: float, message: str) -> ToolChunk:
    return ToolChunk(
        content=[],
        state=ToolResultState.RUNNING,
        is_last=False,
        metadata={"mcp_progress": {"progress": progress, "total": total, "message": message}},
    )


class _StreamingLegacyTool:
    name = "stream_tool"
    description = "yields progress then a result"
    is_read_only = True

    def __init__(self) -> None:
        self.consumed = 0

    async def ainvoke(self, arguments: dict[str, Any] | None = None):
        async def _gen():
            for index in (1, 2):
                self.consumed += 1
                yield _progress_chunk(float(index), 2.0, f"step {index}")
            yield {"success": True, "content": "final"}

        return _gen()


class _PlainLegacyTool:
    name = "plain_tool"
    description = "returns a plain value"
    is_read_only = True

    async def ainvoke(self, arguments: dict[str, Any] | None = None):
        return "plain-result"


async def test_streaming_tool_returns_async_generator_with_progress():
    legacy = _StreamingLegacyTool()
    spec = runtime_tool_spec_from_legacy_tool(legacy, source_type="mcp")
    tool = runtime_tool_from_spec(spec)

    produced = await tool()

    assert inspect.isasyncgen(produced), "生成器工具必须把生成器交给 agent，否则进度会被吞掉"
    items = [item async for item in produced]

    assert len(items) == 3, f"应为 2 个进度片 + 1 个最终片：{items!r}"
    assert [item.metadata["mcp_progress"]["progress"] for item in items[:2]] == [1.0, 2.0]
    for item in items[:2]:
        assert item.is_last is False
        assert item.content == []

    final = items[-1]
    assert final.is_last is True
    assert final.state == ToolResultState.SUCCESS
    assert "final" in final.content[0].text
    assert final.metadata.get("nanzi_call_id"), "最终片必须带内部调用凭证，供前端与证据链关联"


async def test_non_streaming_tool_keeps_single_chunk(monkeypatch):
    """没有进度的工具必须还是「一个 ToolChunk」，事件序列与改造前一致。"""
    spec = runtime_tool_spec_from_legacy_tool(_PlainLegacyTool(), source_type="static")
    tool = runtime_tool_from_spec(spec)

    produced = await tool()

    assert not inspect.isasyncgen(produced), "普通工具不应被改成生成器"
    assert isinstance(produced, ToolChunk)
    assert produced.state == ToolResultState.SUCCESS
    assert produced.content[0].text == "plain-result"


async def test_streaming_records_evidence_once_with_final_result(monkeypatch):
    recorded: list[Any] = []

    def _fake_record(**kwargs):
        recorded.append(kwargs.get("result"))
        return None

    monkeypatch.setattr(runtime_tools, "_record_evidence_result", _fake_record)

    legacy = _StreamingLegacyTool()
    spec = runtime_tool_spec_from_legacy_tool(legacy, source_type="mcp")
    tool = runtime_tool_from_spec(spec)
    produced = await tool()
    [item async for item in produced]

    assert recorded == [{"success": True, "content": "final"}], (
        f"证据必须只记一次且取最终业务结果，实际：{recorded!r}"
    )


async def test_streaming_emits_single_success_audit(monkeypatch):
    audits: list[str] = []

    async def _fake_emit(self, event):
        audits.append(event.status)

    monkeypatch.setattr(runtime_tools.RuntimeToolSpec, "_emit_audit", _fake_emit)

    spec = runtime_tool_spec_from_legacy_tool(_StreamingLegacyTool(), source_type="mcp")
    tool = runtime_tool_from_spec(spec)
    produced = await tool()
    [item async for item in produced]

    assert audits == ["start", "success"], f"审计必须是一次开始 + 一次结束，实际：{audits!r}"


async def test_streaming_failure_becomes_error_chunk():
    class _BoomTool:
        name = "boom_tool"
        description = "raises inside the generator"

        async def ainvoke(self, arguments: dict[str, Any] | None = None):
            async def _gen():
                yield _progress_chunk(1.0, 2.0, "step 1")
                raise RuntimeError("upstream 500")

            return _gen()

    spec = runtime_tool_spec_from_legacy_tool(_BoomTool(), source_type="mcp")
    tool = runtime_tool_from_spec(spec)
    produced = await tool()

    items = [item async for item in produced]

    assert items[0].metadata["mcp_progress"]["progress"] == 1.0
    final = items[-1]
    assert final.is_last is True
    assert final.state == ToolResultState.ERROR
    assert "upstream 500" in final.content[0].text
