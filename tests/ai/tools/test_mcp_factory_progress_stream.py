"""MCP 工具包装层：把远端进度变成 AgentScope 的进度分片。

契约（运行时层依赖它）：
* ``_execute`` 返回 **异步生成器**；
* 中间产出 ``ToolChunk(content=[], state=RUNNING, is_last=False,
  metadata={"mcp_progress": {...}})``；
* 最后一个产出物是 **业务结果本身**（与改造前 ``return`` 的完全一致）。

超时取平台配置快照 ``AgentContext.agent_max_toolcall_timeout_seconds``，
让 MCP 单次调用与其它工具共用同一个旋钮。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

import pytest

from app.models.mcp import McpToolCache
from app.services.ai.tools import mcp_factory
from app.services.ai.tools.mcp_client import McpClientService
from app.services.ai.tools.mcp_factory import McpToolFactory

pytestmark = pytest.mark.no_infrastructure


def _tool_record(tool_name: str = "srv:probe") -> McpToolCache:
    return McpToolCache(
        server_id="srv-1",
        tool_name=tool_name,
        tool_description="probe tool",
        parameter_schema=json.dumps(
            {"type": "object", "properties": {"query": {"type": "string"}}}
        ),
    )


def _patch_call(monkeypatch, *, progress: list[tuple[float, float, str]], result: Any):
    captured: dict[str, Any] = {}

    async def _fake_call_remote_tool(cls_or_self=None, **kwargs):
        captured.update(kwargs)
        callback = kwargs.get("progress_callback")
        for item in progress:
            if callback is not None:
                await callback(*item)
            await asyncio.sleep(0.005)
        return result

    # call_remote_tool 是 classmethod；替换成普通函数后调用方仍按
    # ``McpClientService.call_remote_tool(...)`` 传参，直接吃掉 kwargs 即可。
    monkeypatch.setattr(
        McpClientService,
        "call_remote_tool",
        classmethod(lambda cls, **kwargs: _fake_call_remote_tool(**kwargs)),
    )
    return captured


async def test_execute_streams_progress_then_final_result(monkeypatch):
    captured = _patch_call(
        monkeypatch,
        progress=[(1.0, 3.0, "step 1"), (2.0, 3.0, "step 2"), (3.0, 3.0, "step 3")],
        result={"success": True, "content": "done"},
    )

    tool = McpToolFactory.create_tool(_tool_record())
    produced = await tool.ainvoke({"query": "x"})

    assert inspect.isasyncgen(produced), (
        "MCP 工具必须返回异步生成器，否则 AgentScope 没有机会把进度转成事件"
    )

    items = [item async for item in produced]

    assert items[-1] == {"success": True, "content": "done"}, "最后一个产出物必须是业务结果"
    progress_chunks = items[:-1]
    assert len(progress_chunks) == 3, f"进度分片数量不对：{items!r}"
    assert [chunk.metadata["mcp_progress"]["progress"] for chunk in progress_chunks] == [
        1.0,
        2.0,
        3.0,
    ]
    assert progress_chunks[0].metadata["mcp_progress"]["total"] == 3.0
    assert progress_chunks[0].metadata["mcp_progress"]["message"] == "step 1"

    for chunk in progress_chunks:
        assert chunk.is_last is False, "进度分片不能是最后一片，否则会被当成最终结果"
        assert chunk.content == [], "进度分片不携带内容，避免污染工具输出"
        assert str(chunk.state) in {"running", "ToolResultState.RUNNING"}, chunk.state

    assert captured["server_id"] == "srv-1"
    assert captured["tool_name"] == "probe", "执行闭包必须用远端原始工具名（去掉 server 前缀）"
    assert captured["progress_callback"] is not None


async def test_execute_without_progress_yields_only_result(monkeypatch):
    _patch_call(monkeypatch, progress=[], result="plain text")

    tool = McpToolFactory.create_tool(_tool_record())
    produced = await tool.ainvoke({})
    items = [item async for item in produced]

    assert items == ["plain text"], f"没有进度时不应产生任何额外分片：{items!r}"


async def test_execute_uses_agent_context_timeout(monkeypatch):
    captured = _patch_call(monkeypatch, progress=[], result="ok")

    from app.core.context import AgentContext, agent_context

    context = AgentContext(agent_id="a-1", agent_name="probe")
    context.agent_max_toolcall_timeout_seconds = 42.0
    token = agent_context.set(context)
    try:
        tool = McpToolFactory.create_tool(_tool_record())
        produced = await tool.ainvoke({})
        [item async for item in produced]
    finally:
        agent_context.reset(token)

    assert captured["timeout_seconds"] == 42.0, (
        "MCP 单次调用必须跟随 agent_max_toolcall_timeout 快照，不能另有一套超时"
    )


async def test_execute_falls_back_to_configured_timeout(monkeypatch):
    captured = _patch_call(monkeypatch, progress=[], result="ok")

    async def _fake_load() -> float:
        return 275.0

    monkeypatch.setattr(mcp_factory, "load_agent_max_toolcall_timeout", _fake_load)

    tool = McpToolFactory.create_tool(_tool_record())
    produced = await tool.ainvoke({})
    [item async for item in produced]

    assert captured["timeout_seconds"] == 275.0, (
        "没有请求上下文（例如 MCP 管理台试跑）时必须回退到系统配置，而不是写死常量"
    )


async def test_execute_propagates_remote_failure(monkeypatch):
    async def _boom(cls_or_self=None, **kwargs):
        raise RuntimeError("MCP tool 'probe' failed: upstream 500")

    monkeypatch.setattr(
        McpClientService,
        "call_remote_tool",
        classmethod(lambda cls, **kwargs: _boom(**kwargs)),
    )

    tool = McpToolFactory.create_tool(_tool_record())
    produced = await tool.ainvoke({})

    with pytest.raises(RuntimeError, match="upstream 500"):
        [item async for item in produced]


async def test_direct_invocation_consumes_generator(monkeypatch):
    """管理台「测试工具」这类直接调用方，必须拿到业务值而不是生成器对象。"""
    from app.services.ai.tools.tool_compat import resolve_tool_invocation_result

    _patch_call(monkeypatch, progress=[(1.0, 2.0, "step 1")], result={"success": True})

    tool = McpToolFactory.create_tool(_tool_record())
    result = await resolve_tool_invocation_result(await tool.ainvoke({}))

    assert result == {"success": True}


async def test_resolve_helper_passes_plain_values_through():
    from app.services.ai.tools.tool_compat import resolve_tool_invocation_result

    assert await resolve_tool_invocation_result("plain") == "plain"
    assert await resolve_tool_invocation_result({"a": 1}) == {"a": 1}


async def test_cancelling_consumer_cancels_remote_call(monkeypatch):
    """用户点停止时，取消必须传到远端调用，不能把它晾在 SSE 连接上。"""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _slow(cls_or_self=None, **kwargs):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "never"

    monkeypatch.setattr(
        McpClientService,
        "call_remote_tool",
        classmethod(lambda cls, **kwargs: _slow(**kwargs)),
    )

    tool = McpToolFactory.create_tool(_tool_record())
    produced = await tool.ainvoke({})
    first_chunk = asyncio.ensure_future(produced.__anext__())
    await started.wait()

    first_chunk.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_chunk

    assert cancelled.is_set(), "生成器被取消后，远端调用必须收到取消"


async def test_non_numeric_progress_is_ignored(monkeypatch):
    """第三方服务端发出畸形通知时，丢弃即可，绝不能让工具失败。"""

    async def _fake(cls_or_self=None, **kwargs):
        callback = kwargs["progress_callback"]
        await callback("oops", 10, "bad")  # 非数字：跳过
        await callback(True, 10, "bool")  # bool 是 int 子类：同样跳过
        await callback(1, 10, "ok")
        return "done"

    monkeypatch.setattr(
        McpClientService,
        "call_remote_tool",
        classmethod(lambda cls, **kwargs: _fake(**kwargs)),
    )

    tool = McpToolFactory.create_tool(_tool_record())
    produced = await tool.ainvoke({})
    items = [item async for item in produced]

    assert [item.metadata["mcp_progress"]["progress"] for item in items[:-1]] == [1.0]
    assert items[-1] == "done"
