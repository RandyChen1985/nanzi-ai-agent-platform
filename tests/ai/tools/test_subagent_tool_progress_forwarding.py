"""子代理事件桥接：`tool_progress` 必须穿透到主聊天，且不能污染委派正文。

背景：`_consume_sub_agent_stream` 是白名单转发器（log / browser_session / citation），
新事件类型默认被静默丢弃——所以「委派智能体 → 子代理内的 MCP 工具」永远看不到进度。

额外陷阱：`_extract_delegation_text` 会读取 `message` 键，而进度帧恰好带 `message`
（进度文案）。若不在正文提取之前拦下，`"检索数据集 3/10"` 会被拼成子代理最终回答。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.context import AgentContext
from app.services.ai.tools.agent_delegate_tool import _consume_sub_agent_stream

pytestmark = pytest.mark.no_infrastructure


def _ctx() -> AgentContext:
    ctx = AgentContext(agent_id="main-agent", agent_name="主助手")
    ctx.event_queue = asyncio.Queue()
    return ctx


async def _drain(queue: asyncio.Queue) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def _stream(*chunks: dict[str, Any]):
    for chunk in chunks:
        yield chunk


async def test_tool_progress_is_forwarded_to_main_stream():
    ctx = _ctx()
    sub_stream = _stream(
        {"type": "tool_progress", "id": "tc-1", "progress": 3, "total": 10, "message": "检索数据集"},
        {"type": "log", "id": "tc-1", "title": "工具完成", "details": "ok", "status": "success"},
    )

    output, interrupt = await _consume_sub_agent_stream(
        sub_stream,
        main_ctx=ctx,
        sub_display_name="火车票",
    )

    forwarded = await _drain(ctx.event_queue)
    progress_frames = [item for item in forwarded if item["type"] == "tool_progress"]
    assert progress_frames == [
        {"type": "tool_progress", "id": "tc-1", "progress": 3, "total": 10, "message": "检索数据集"}
    ]
    assert interrupt is None
    assert output == "", "进度帧不能进入委派正文"


async def test_progress_message_is_not_treated_as_delegation_text():
    """`message` 键会被 `_extract_delegation_text` 当成正文，必须提前拦下。"""
    ctx = _ctx()
    sub_stream = _stream(
        {"type": "tool_progress", "id": "tc-1", "progress": 1, "total": 3, "message": "正在生成 SQL"},
        {"type": "answer_delta", "content": "最终答案"},
    )

    output, _ = await _consume_sub_agent_stream(
        sub_stream,
        main_ctx=ctx,
        sub_display_name="数据分析",
    )

    assert output == "最终答案"
    assert "正在生成 SQL" not in output


async def test_progress_without_event_queue_does_not_crash():
    ctx = _ctx()
    ctx.event_queue = None
    sub_stream = _stream(
        {"type": "tool_progress", "id": "tc-1", "progress": 1, "total": 3, "message": "x"},
    )

    output, _ = await _consume_sub_agent_stream(
        sub_stream,
        main_ctx=ctx,
        sub_display_name="任意",
    )

    assert output == ""
