"""出站 MCP 工具调用的进度透传（mcp 2.x ``progress_callback``）。

远端发 ``notifications/progress`` 时，``McpClientService`` 必须把通知原样交给调用方
传入的回调，让上层能把它变成前端的工具卡进度；不传回调时行为必须与现在完全一致。

同时锁定：单次工具调用的超时来自平台配置（``agent_max_toolcall_timeout`` 快照），
不再是写死的 120 秒 —— 否则长工具会在进度走到一半时被静默掐断。
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

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from mcp import ClientSession
from mcp.client.sse import sse_client

from app.services.mcp.mcp_auth_policy import build_mcp_headers  # noqa: F401  (导入即校验依赖可用)
from app.services.ai.tools.mcp_client import (
    DEFAULT_MCP_TOOL_CALL_TIMEOUT,
    _call_tool_with_progress,
)

pytestmark = pytest.mark.no_infrastructure

PROTOCOL_VERSION = "2025-06-18"
PROGRESS_TOOL = "probe_progress"
SLOW_TOOL = "probe_slow"


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": PROGRESS_TOOL,
            "description": "发三条进度后返回",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": SLOW_TOOL,
            "description": "睡很久，用来验证超时口径",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _build_app() -> FastAPI:
    app = FastAPI()
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

        method = body.get("method")
        if body.get("id") is None:
            return Response(status_code=202)

        if method == "initialize":
            await queue.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {
                            "protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "progress-probe", "version": "1.0.0"},
                        },
                    }
                )
            )
            return Response(status_code=202)

        if method == "tools/list":
            await queue.put(
                json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": _tools()}})
            )
            return Response(status_code=202)

        if method == "tools/call":
            params = body.get("params") or {}
            name = params.get("name")
            token = ((params.get("_meta") or {}).get("progressToken"))

            if name == SLOW_TOOL:
                await asyncio.sleep(5)
            elif name == PROGRESS_TOOL and token is not None:
                for index in (1, 2, 3):
                    await queue.put(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "method": "notifications/progress",
                                "params": {
                                    "progressToken": token,
                                    "progress": index,
                                    "total": 3,
                                    "message": f"step {index}",
                                },
                            }
                        )
                    )
                    await asyncio.sleep(0.01)

            await queue.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {
                            "content": [{"type": "text", "text": "done"}],
                            "isError": False,
                        },
                    }
                )
            )
            return Response(status_code=202)

        await queue.put(json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {}}))
        return Response(status_code=202)

    return app


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


@contextlib.contextmanager
def _running_server() -> Iterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        _build_app(),
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


@contextlib.asynccontextmanager
async def _connected_session(base_url: str):
    async with sse_client(f"{base_url}/sse") as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


# --------------------------------------------------------------------------- #
# 1. 远端进度必须原样交给回调
# --------------------------------------------------------------------------- #
async def test_call_tool_forwards_remote_progress_notifications():
    received: list[tuple[float, float | None, str | None]] = []

    async def _on_progress(progress: float, total: float | None, message: str | None) -> None:
        received.append((progress, total, message))

    with _running_server() as base_url:
        async with _connected_session(base_url) as session:
            response = await _call_tool_with_progress(
                session,
                PROGRESS_TOOL,
                {},
                progress_callback=_on_progress,
                timeout_seconds=10,
            )

    assert [(item[0], item[1], item[2]) for item in received] == [
        (1.0, 3.0, "step 1"),
        (2.0, 3.0, "step 2"),
        (3.0, 3.0, "step 3"),
    ], f"远端 progress 未被透传，收到 {received!r}"
    assert "".join(getattr(item, "text", "") for item in response.content) == "done"


# --------------------------------------------------------------------------- #
# 2. 不传回调：行为与今天一致（不发令牌、结果不变）
# --------------------------------------------------------------------------- #
async def test_call_tool_without_callback_keeps_legacy_behaviour():
    with _running_server() as base_url:
        async with _connected_session(base_url) as session:
            response = await _call_tool_with_progress(
                session,
                PROGRESS_TOOL,
                {},
                timeout_seconds=10,
            )

    assert "".join(getattr(item, "text", "") for item in response.content) == "done"
    assert getattr(response, "is_error", False) is False


# --------------------------------------------------------------------------- #
# 3. 超时来自调用方配置，不是写死的 120 秒
# --------------------------------------------------------------------------- #
async def test_call_tool_passes_caller_timeout_to_wait_for(monkeypatch):
    """超时值必须原样进入 ``asyncio.wait_for``：调用方（平台配置快照）说了算。

    注：本用例只校验接线，不校验墙钟——mcp 2.x 的请求取消是 shielded 的，
    ``wait_for`` 到点后会先标记取消、等远端真正返回才抛出（实测 0.3s 超时、
    5s 才抛）。这是升级前就存在的行为，不在本次改动范围内。
    """
    captured: dict[str, Any] = {}

    class _Session:
        async def call_tool(self, *args, **kwargs):
            return "raw"

    async def _fake_wait_for(awaitable, timeout=None):
        captured["timeout"] = timeout
        close = getattr(awaitable, "close", None)
        if close is not None:
            close()
        return "sentinel"

    monkeypatch.setattr("app.services.ai.tools.mcp_client.asyncio.wait_for", _fake_wait_for)

    assert await _call_tool_with_progress(_Session(), "t", {}, timeout_seconds=42) == "sentinel"
    assert captured["timeout"] == 42

    assert await _call_tool_with_progress(_Session(), "t", {}) == "sentinel"
    assert captured["timeout"] == DEFAULT_MCP_TOOL_CALL_TIMEOUT


async def test_call_tool_still_times_out_for_slow_tool():
    """行为不变：慢工具最终仍以 TimeoutError 结束（重连判定依赖该异常类型）。"""
    with _running_server() as base_url:
        async with _connected_session(base_url) as session:
            with pytest.raises(asyncio.TimeoutError):
                await _call_tool_with_progress(
                    session,
                    SLOW_TOOL,
                    {},
                    timeout_seconds=0.3,
                )


def test_default_timeout_is_not_the_old_hardcoded_120():
    """写死的 120s 曾让长工具在进度走到一半时被掐断，默认值必须可覆盖且不等于旧常量。"""
    assert DEFAULT_MCP_TOOL_CALL_TIMEOUT > 0
    assert DEFAULT_MCP_TOOL_CALL_TIMEOUT != 120.0


# --------------------------------------------------------------------------- #
# 4. Direct HTTP 网关同一口径：超时也来自调用方
# --------------------------------------------------------------------------- #
async def test_direct_http_tool_call_passes_timeout(monkeypatch):
    """SSE-to-HTTP 降级与原生 Direct HTTP 都走 ``_call_direct_http_tool``。

    进度只在 SSE 长连接上存在，但超时不能因此两套口径：平台配 300s，直连网关
    也必须按 300s 发这次请求，而不是沿用 httpx client 的 120s 默认值。
    """
    from app.services.ai.tools.mcp_client import McpClientService

    captured: dict[str, Any] = {}

    async def _fake_ensure(session_mgr):
        return None

    async def _fake_rpc(session_mgr, method, params, is_notification=False, retry_count=0, timeout=None):
        captured["method"] = method
        captured["timeout"] = timeout
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(McpClientService, "_ensure_direct_http_initialized", _fake_ensure)
    monkeypatch.setattr(McpClientService, "_direct_http_rpc", _fake_rpc)

    result = await McpClientService._call_direct_http_tool(
        object(), "probe", {}, timeout_seconds=300
    )

    assert captured["method"] == "tools/call"
    assert captured["timeout"] == 300
    assert result == "ok"
