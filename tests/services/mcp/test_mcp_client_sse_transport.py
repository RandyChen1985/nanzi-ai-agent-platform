"""出站 MCP 的 SSE 传输必须在 mcp 2.x 下真正建连（回归：静默降级 Direct HTTP）。

背景：mcp 2.x 把客户端传输整体切到 ``httpx2``，``sse_client(httpx_client_factory=...)``
要求工厂返回 ``httpx2.AsyncClient``。升级到 ``mcp==2.3.0`` 时平台工厂仍返回
``httpx.AsyncClient``，而 sse_client 会把 ``httpx2.Timeout`` 传给工厂、再交给 httpx：

    TypeError: unsupported operand type(s) for +: 'float' and 'Timeout'

这个异常在 ``McpSseSession.connect()`` 里被 ``except Exception`` 吞成一条 warning，
然后静默降级成 Direct HTTP —— 对只支持 SSE 的外部 MCP 服务，工具同步与调用全部失败，
而管理页只看到「同步失败」，没有任何线索指向传输层。

这里锁两件事：
1. 工厂返回的必须是 httpx2 客户端，并且不吃 httpx2 的 Timeout 就炸（单测，毫秒级）；
2. 平台真实的 ``McpSseSession.connect()`` 能对一个真实 SSE 端点完成 initialize +
   tools/list，且 ``is_direct_http`` 必须保持 False（端到端，起一个最小 SSE 服务）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import time
import uuid
from typing import Any, Iterator

import httpx2
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from app.services.ai.tools.mcp_client import (
    McpSseSession,
    _create_no_redirect_mcp_http_client,
)

pytestmark = pytest.mark.no_infrastructure

PROTOCOL_VERSION = "2025-06-18"
PROBE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}


# --------------------------------------------------------------------------- #
# 最小 SSE MCP 服务（只实现 initialize / tools/list，够 sse_client 跑通握手）
# --------------------------------------------------------------------------- #
def _build_sse_app() -> FastAPI:
    app = FastAPI()
    # 每个 SSE 流各自一条队列：探测请求与真实连接会各开一条流，共用队列会把
    # 响应投递到已经断开的探测流上。
    streams: dict[str, asyncio.Queue[str]] = {}

    @app.get("/sse")
    async def sse() -> StreamingResponse:
        sid = uuid.uuid4().hex
        queue: asyncio.Queue[str] = asyncio.Queue()
        streams[sid] = queue

        async def gen():
            yield f"event: endpoint\ndata: /messages?session_id={sid}\n\n"
            while True:
                yield f"event: message\ndata: {await queue.get()}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/messages")
    async def messages(request: Request) -> Response:
        sid = request.query_params.get("session_id") or ""
        body = await request.json()
        queue = streams.get(sid)
        if queue is None:
            return Response(status_code=404)
        # 通知（notifications/initialized）没有 id，只回 202，不产生响应帧。
        if body.get("id") is not None:
            await queue.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": _rpc_result(body.get("method")),
                    }
                )
            )
        return Response(status_code=202)

    return app


def _rpc_result(method: str | None) -> dict[str, Any]:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "sse-probe", "version": "1.0.0"},
        }
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "probe_tool",
                    "description": "probe",
                    "inputSchema": PROBE_TOOL_SCHEMA,
                }
            ]
        }
    return {}


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
                raise AssertionError(f"SSE 探针服务未在 {timeout}s 内监听 127.0.0.1:{port}")
            time.sleep(0.05)


@contextlib.contextmanager
def _running_sse_server() -> Iterator[str]:
    """在后台线程跑一个真实 HTTP 服务，返回它的 base_url。"""
    port = _free_port()
    config = uvicorn.Config(
        _build_sse_app(),
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        _wait_until_listening(port)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# --------------------------------------------------------------------------- #
# 1. 工厂契约：必须交付 httpx2 客户端
# --------------------------------------------------------------------------- #
async def test_sse_http_client_factory_returns_httpx2_client():
    client = _create_no_redirect_mcp_http_client(
        headers={"X-Test": "1"},
        timeout=httpx2.Timeout(5.0, read=300.0),
    )
    try:
        assert isinstance(client, httpx2.AsyncClient), (
            "mcp 2.x 的 sse_client 只接受 httpx2 客户端；返回 httpx.AsyncClient 会在"
            " TCP 连接阶段抛 TypeError 并被静默降级"
        )
        assert client.follow_redirects is False, "带凭据的 Header 不能跟随重定向"
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# 2. 端到端：真实 SSE 端点 + 平台自己的会话类
# --------------------------------------------------------------------------- #
async def test_mcp_sse_session_handshakes_over_real_sse_endpoint():
    with _running_sse_server() as base_url:
        session = McpSseSession(
            server_id="probe-sse",
            sse_url=f"{base_url}/sse",
            auth_headers={"X-Test": "1"},
        )
        try:
            await session.connect()

            assert session.is_direct_http is False, (
                "SSE 建连失败后会被静默降级成 Direct HTTP；对 SSE-only 服务等于功能不可用"
            )
            assert session.session is not None

            tools = await session.session.list_tools()
            assert [tool.name for tool in tools.tools] == ["probe_tool"]
            assert tools.tools[0].input_schema == PROBE_TOOL_SCHEMA
        finally:
            await session.close()
