"""子代理思考：作为折叠日志回流，但绝不进入委派正文。

背景：`reasoning_content` 帧原先被整段丢弃。丢弃的两条理由都成立——
① 拼进工具结果会顶到委派结果上限，把真正的结论挤掉；
② 并进父会话正文会让主智能体的回答里混进子代理的推理。
但「用户分不清这段是主代理想的还是子代理想的」是另一个真实问题，
所以改走第三条路：作为 `category="reasoning"` 的 **log 帧**挂进子代理容器，默认折叠。

这一组断言锁的就是这条边界：
- 一定产生日志（否则前端无从标注）；
- 一定不进 `full_output`（否则结论会被挤掉）；
- 多轮思考必须**分开**落（思考轮次之间夹着工具调用，粘成一条会丢掉前面的轮次）；
- 超长要截断（思考是逐 token 累积的，一条上万字的 SSE 帧前端和落库都吃不消）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.context import AgentContext
from app.services.ai.tools.agent_delegate_tool import (
    DELEGATION_SUBAGENT_REASONING_MAX_CHARS,
    _consume_sub_agent_stream,
)

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


def _reasoning_logs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [i for i in items if i.get("type") == "log" and i.get("category") == "reasoning"]


async def test_reasoning_is_forwarded_as_collapsed_log():
    ctx = _ctx()
    sub_stream = _stream(
        {"type": "reasoning_content", "content": "先确认宽表里有没有 "},
        {"type": "reasoning_content", "content": "first_reply_at 字段。"},
        {"type": "answer_delta", "content": "结论如下"},
    )

    output, interrupt = await _consume_sub_agent_stream(
        sub_stream, main_ctx=ctx, sub_display_name="元数据专家"
    )

    logs = _reasoning_logs(await _drain(ctx.event_queue))
    assert len(logs) == 1
    assert logs[0]["details"] == "先确认宽表里有没有 first_reply_at 字段。"
    assert logs[0]["title"] == "[元数据专家] 子代理思考"
    assert logs[0]["reasoning"] is True
    # 关键边界：思考不进委派正文
    assert output == "结论如下"
    assert "first_reply_at" not in output
    assert interrupt is None


async def test_rounds_are_flushed_separately_across_tool_calls():
    """两轮思考中间夹一次工具调用 → 两条独立日志，不能粘成一条。"""
    ctx = _ctx()
    sub_stream = _stream(
        {"type": "reasoning_content", "content": "第一轮想法。"},
        {"type": "log", "id": "tc-1", "title": "执行 SQL", "details": "ok", "status": "success"},
        {"type": "reasoning_content", "content": "第二轮想法。"},
        {"type": "answer_delta", "content": "完成"},
    )

    await _consume_sub_agent_stream(sub_stream, main_ctx=ctx, sub_display_name="查数")

    logs = _reasoning_logs(await _drain(ctx.event_queue))
    assert [log["details"] for log in logs] == ["第一轮想法。", "第二轮想法。"]
    # 两条 id 必须不同，否则前端会当成同一条覆盖掉
    assert logs[0]["id"] != logs[1]["id"]


async def test_trailing_reasoning_without_following_frame_is_still_flushed():
    """最后一段思考后面没有任何帧，也必须落——否则这段就丢了。"""
    ctx = _ctx()
    sub_stream = _stream({"type": "reasoning_content", "content": "收尾时的想法。"})

    await _consume_sub_agent_stream(sub_stream, main_ctx=ctx, sub_display_name="任意")

    logs = _reasoning_logs(await _drain(ctx.event_queue))
    assert [log["details"] for log in logs] == ["收尾时的想法。"]


async def test_overlong_reasoning_is_truncated_with_marker():
    ctx = _ctx()
    sub_stream = _stream(
        {"type": "reasoning_content", "content": "甲" * (DELEGATION_SUBAGENT_REASONING_MAX_CHARS + 500)},
        {"type": "answer_delta", "content": "完"},
    )

    await _consume_sub_agent_stream(sub_stream, main_ctx=ctx, sub_display_name="任意")

    logs = _reasoning_logs(await _drain(ctx.event_queue))
    assert len(logs) == 1
    assert logs[0]["details"].endswith("…（思考过长，已截断）")
    assert len(logs[0]["details"]) < DELEGATION_SUBAGENT_REASONING_MAX_CHARS + 60


async def test_reasoning_log_carries_subagent_metadata():
    """带上 subagent 元数据，前端才能把它挂进子代理容器而不是主链路。"""
    ctx = _ctx()
    sub_stream = _stream(
        {"type": "reasoning_content", "content": "想法。"},
        {"type": "answer_delta", "content": "完"},
    )

    await _consume_sub_agent_stream(
        sub_stream,
        main_ctx=ctx,
        sub_display_name="元数据专家",
        subagent_metadata={"run_id": "subrun_abc", "display_name": "元数据专家"},
        run_id="subrun_abc",
    )

    logs = _reasoning_logs(await _drain(ctx.event_queue))
    assert logs[0]["subagent"]["run_id"] == "subrun_abc"


async def test_thinking_status_frame_does_not_produce_empty_log():
    """`thinking` 只是「正在想」的状态帧，没有正文，不该产出空日志。"""
    ctx = _ctx()
    sub_stream = _stream(
        {"type": "thinking", "status": "continuing"},
        {"type": "answer_delta", "content": "完"},
    )

    await _consume_sub_agent_stream(sub_stream, main_ctx=ctx, sub_display_name="任意")

    assert _reasoning_logs(await _drain(ctx.event_queue)) == []


async def test_no_event_queue_does_not_crash():
    ctx = _ctx()
    ctx.event_queue = None
    sub_stream = _stream(
        {"type": "reasoning_content", "content": "想法。"},
        {"type": "answer_delta", "content": "完"},
    )

    output, _ = await _consume_sub_agent_stream(sub_stream, main_ctx=ctx, sub_display_name="任意")

    assert output == "完"
