"""Streamable HTTP 传输：mcp 2.x 的标准传输，也是 HTTP 下唯一能收到进度的通道。

背景（实测）：FastMCP 的 ``/mcp`` 端点对裸 GET 会挂成长连接（``text/event-stream``），
旧的「先探测 SSE」逻辑会先卡满探测超时、再白等一次 SSE 握手，最后降级到 Direct HTTP。
Direct HTTP 只能 POST 一个 JSON-RPC，收不到 ``notifications/progress``——所以平台自己的
Echo（以及所有 mcp 2.x 服务端）在修复前：

* 首次调用白白多花十几秒（探测 5s + SSE 握手 10s）；
* 进度帧一条都收不到，工具卡永远只有转圈和秒表。

修复：``connect()`` 先试 Streamable HTTP，失败再回退 SSE，最后才是 Direct HTTP。
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from typing import Any, Iterator

import pytest
import uvicorn
from mcp.server.mcpserver import Context, MCPServer

from app.services.ai.tools.mcp_client import McpSseSession

pytestmark = pytest.mark.no_infrastructure

STREAMABLE_TOOL = "probe_streamable"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_listening(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            if time.monotonic() >= deadline:
                raise AssertionError(f"探针服务未在 {timeout}s 内监听 127.0.0.1:{port}")
            time.sleep(0.05)


def _build_server() -> MCPServer:
    server = MCPServer("streamable-probe")

    @server.tool(name=STREAMABLE_TOOL, description="逐步上报进度")
    async def probe_streamable(ctx: Context, steps: int = 3) -> str:
        for index in range(1, steps + 1):
            await ctx.report_progress(index, steps, f"step {index}/{steps}")
            await asyncio.sleep(0.01)
        return "done"

    return server


@contextlib.contextmanager
def _running_streamable_server() -> Iterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        _build_server().streamable_http_app(),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_until_listening(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


async def test_connect_prefers_streamable_http():
    """mcp 2.x 服务端必须走真实会话，而不是降级成功能残缺的 Direct HTTP。"""
    with _running_streamable_server() as url:
        session_mgr = McpSseSession("probe", url, {})
        try:
            await session_mgr.connect()

            assert session_mgr.is_direct_http is False, (
                "Streamable HTTP 端点不该被降级为 Direct HTTP"
            )
            assert session_mgr.session is not None
        finally:
            await session_mgr.close()


async def test_streamable_session_forwards_progress_notifications():
    """端到端：走 McpSseSession 的会话，远端 progress 必须能被收到。"""
    from app.services.ai.tools.mcp_client import _call_tool_with_progress

    with _running_streamable_server() as url:
        session_mgr = McpSseSession("probe", url, {})
        try:
            await session_mgr.connect()
            assert session_mgr.session is not None

            seen: list[tuple[float, float | None, str | None]] = []

            async def _on_progress(
                progress: float,
                total: float | None,
                message: str | None,
            ) -> None:
                seen.append((progress, total, message))

            response = await _call_tool_with_progress(
                session_mgr.session,
                STREAMABLE_TOOL,
                {"steps": 3},
                progress_callback=_on_progress,
                timeout_seconds=30.0,
            )

            text = "".join(
                getattr(item, "text", "") for item in (response.content or [])
            )
            assert text == "done"
            assert [item[0] for item in seen] == [1.0, 2.0, 3.0], (
                f"远端进度没有透传：{seen}"
            )
        finally:
            await session_mgr.close()


async def test_connect_records_no_fallback_for_streamable():
    """回归：连接成功时不能把 is_direct_http 置 True（会让调用侧跳过会话）。"""
    with _running_streamable_server() as url:
        session_mgr = McpSseSession("probe", url, {})
        try:
            await session_mgr.connect()
            assert session_mgr.is_direct_http is False
            # 第二次 connect 是幂等的，不能重连成 Direct HTTP
            await session_mgr.connect()
            assert session_mgr.is_direct_http is False
        finally:
            await session_mgr.close()


async def test_factory_generator_yields_progress_from_real_server(monkeypatch):
    """端到端：真实 streamable 服务端的 progress 必须变成工厂生成器的进度片。

    覆盖 ``stream_remote_tool_with_progress`` 的队列 / 节流 / 收尾循环；
    与上一个用例（``call_remote_tool`` 内部）合起来就是完整后端链路。
    """
    from agentscope.message import ToolResultState
    from agentscope.tool import ToolChunk
    from mcp import ClientSession as _ClientSession
    from mcp.client.streamable_http import streamable_http_client as _shc

    from app.services.ai.tools.mcp_client import (
        McpClientService,
        _call_tool_with_progress,
    )
    from app.services.ai.tools.mcp_factory import stream_remote_tool_with_progress

    captured: dict[str, Any] = {}

    with _running_streamable_server() as url:

        async def _fake_call_remote_tool(cls_or_self=None, **kwargs: Any) -> Any:
            captured["tool_name"] = kwargs["tool_name"]
            async with _shc(url) as (read, write):
                async with _ClientSession(read, write) as session:
                    await session.initialize()
                    response = await _call_tool_with_progress(
                        session,
                        kwargs["tool_name"],
                        kwargs["arguments"],
                        progress_callback=kwargs.get("progress_callback"),
                        timeout_seconds=30.0,
                    )
                    return "".join(
                        getattr(block, "text", "") for block in (response.content or [])
                    )

        monkeypatch.setattr(
            McpClientService,
            "call_remote_tool",
            classmethod(lambda cls, **kwargs: _fake_call_remote_tool(**kwargs)),
        )

        items = [
            item
            async for item in stream_remote_tool_with_progress(
                server_id="probe",
                tool_name=STREAMABLE_TOOL,
                arguments={"steps": 4},
                timeout_seconds=30.0,
            )
        ]

    progress_chunks = [
        item
        for item in items
        if isinstance(item, ToolChunk)
        and (item.metadata or {}).get("mcp_progress")
        and not item.is_last
    ]
    assert len(progress_chunks) == 4, (
        f"真实服务端的 4 条进度没有全部变成进度片：{len(progress_chunks)}"
    )
    assert [c.metadata["mcp_progress"]["progress"] for c in progress_chunks] == [
        1.0,
        2.0,
        3.0,
        4.0,
    ]
    assert all(c.state == ToolResultState.RUNNING for c in progress_chunks)
    assert items[-1] == "done", "最后一片必须是业务结果本身"
    assert captured["tool_name"] == STREAMABLE_TOOL


async def test_connect_passes_relaxed_sse_event_size_limit(monkeypatch):
    """回归：单个 SSE event 的上限必须显式放宽，不能吃 SDK 默认的 1 MiB。

    图像 / 文件类工具把结果 base64 内联在**一个** SSE event 的 ``data`` 里：
    一张 1024×1024 的 PNG base64 后就有 2～5 MB，SDK 默认值必然打成
    「Server-sent event exceeded the 1048576 byte limit」（DashScope
    ``text_generate_image`` 的实测症状）。该报错外面裹着
    ``MCP tool call failed:``，看起来像传输故障，实际是结果体积超限。

    这里只锁「参数确实传给了 SDK」——体积行为由 SDK 自己负责，端到端再由
    真实服务端用例兜底。
    """
    from unittest.mock import AsyncMock

    from app.services.ai.tools import mcp_client as mcp_client_module
    from app.services.ai.tools.mcp_client import DEFAULT_MCP_MAX_SSE_EVENT_SIZE

    captured: dict[str, Any] = {}

    class _FakeCM:
        def __init__(self, value: Any) -> None:
            self._value = value

        async def __aenter__(self) -> Any:
            return self._value

        async def __aexit__(self, *exc_info: Any) -> bool:
            return False

    class _FakeSession:
        async def initialize(self) -> None:
            return None

    def _fake_streamable_http_client(url: str, **kwargs: Any) -> _FakeCM:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeCM(("read", "write"))

    # connect() 先做 TCP 预检，不可达会直接降级、走不到 streamable 分支
    monkeypatch.setattr(
        McpSseSession,
        "_tcp_endpoint_reachable",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        mcp_client_module, "streamable_http_client", _fake_streamable_http_client
    )
    monkeypatch.setattr(
        mcp_client_module,
        "_create_no_redirect_mcp_http_client",
        lambda **kwargs: _FakeCM(object()),
    )
    monkeypatch.setattr(
        mcp_client_module,
        "ClientSession",
        lambda read, write: _FakeCM(_FakeSession()),
    )

    session_mgr = McpSseSession("probe", "https://example.test/mcp", {})
    try:
        await session_mgr.connect()

        assert session_mgr.is_direct_http is False, "应走 Streamable HTTP 而非降级"
        assert captured["max_sse_event_size"] == DEFAULT_MCP_MAX_SSE_EVENT_SIZE
        assert DEFAULT_MCP_MAX_SSE_EVENT_SIZE > 1024 * 1024, (
            "上限必须大于 SDK 默认的 1 MiB，否则图像类工具照样被打爆"
        )
    finally:
        await session_mgr.close()
