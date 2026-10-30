"""SSE 映射：AgentScope 的 ``mcp_progress`` CustomEvent → 前端 ``tool_progress``。

约定：
* 只认 ``name == "mcp_progress"``，其它自定义事件必须原样静默丢弃；
* 产出的是**瞬态**帧，按 ``tool_call_id`` 更新已有工具卡，不新建卡片、不落库。
"""

from __future__ import annotations

import pytest
from agentscope.event import CustomEvent

from app.services.ai.runtime.agentscope.event_stream import map_standard_agentscope_event

pytestmark = pytest.mark.no_infrastructure


async def _collect(event) -> list[dict]:
    return [
        chunk
        async for chunk in map_standard_agentscope_event(
            event,
            state={},
            emit_observability=False,
        )
    ]


async def test_custom_mcp_progress_becomes_tool_progress_frame():
    event = CustomEvent(
        name="mcp_progress",
        value={
            "progress": 4,
            "total": 10,
            "message": "检索数据集",
            "reply_id": "reply-1",
            "tool_call_id": "tc-1",
        },
    )

    frames = await _collect(event)

    assert frames == [
        {
            "type": "tool_progress",
            "id": "tc-1",
            "progress": 4.0,
            "total": 10.0,
            "message": "检索数据集",
        }
    ]


async def test_custom_mcp_progress_without_total_is_supported():
    event = CustomEvent(
        name="mcp_progress",
        value={"progress": 99, "total": None, "message": None, "tool_call_id": "tc-2"},
    )

    frames = await _collect(event)

    assert len(frames) == 1
    assert frames[0]["total"] is None
    assert frames[0]["progress"] == 99.0
    assert frames[0]["message"] is None


async def test_unknown_custom_event_is_ignored():
    event = CustomEvent(name="something_else", value={"tool_call_id": "tc-3"})

    assert await _collect(event) == []


async def test_mcp_progress_without_tool_call_id_is_ignored():
    event = CustomEvent(name="mcp_progress", value={"progress": 1, "total": 2})

    assert await _collect(event) == [], "没有 tool_call_id 就无法定位卡片，必须丢弃"
