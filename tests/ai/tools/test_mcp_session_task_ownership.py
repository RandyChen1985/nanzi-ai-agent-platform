"""MCP 共享会话的 anyio cancel scope 必须由「建链的那个 task」退出。

背景（生产现象）：新建 MCP 服务后的自动同步会为它建会话，日志里紧跟着一条

    RuntimeError: Attempted to exit a cancel scope that isn't the current
    tasks's current cancel scope

响应其实已经 200 返回，炸的是请求收尾阶段。原因是 mcp 2.x 的
``streamable_http_client`` / ``sse_client`` 内部 ``anyio.create_task_group()``
会把 cancel scope 压在**驱动它的那个 task** 的栈上；而 ``McpClientService``
把会话缓存起来跨请求复用，于是请求 task 结束、``BaseHTTPMiddleware`` 的
worker task 退自己的 scope 时，栈顶还压着 MCP 的那一个。

本用例锁定修复后的不变式：
1. 传输层（含 anyio task group）在专属 owner task 里建立，**不在请求 task 里**；
2. 请求正常返回，收尾阶段不再抛 cancel scope 相关的 RuntimeError；
3. 缓存命中的第二次请求同样不受影响。
"""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio
import httpx
import pytest
from fastapi import FastAPI

from app.core.middleware import AccessLogMiddleware
from app.services.ai.tools.mcp_client import McpSseSession

pytestmark = pytest.mark.no_infrastructure

SERVER_ID = "owner-regression"
MCP_URL = "https://example.test/mcp"

#: 观测点：谁在跑请求、谁在建链
_OBSERVED: dict = {}
#: 等价于 McpClientService._sessions：跨请求复用的共享会话缓存
_SESSIONS: dict = {}


class _FakeTaskGroupTransport:
    """与 mcp 2.3.0 ``streamable_http_client`` 同构的最小传输。

    关键点就是 ``anyio.create_task_group()``：它会在当前 task 上压一个 cancel
    scope，直到 ``__aexit__``。退出时显式 cancel（SDK 也是这么做的），避免
    子任务常驻把退出卡住。
    """

    def __init__(self) -> None:
        self._cm = None
        self._tg = None

    async def __aenter__(self):
        _OBSERVED["transport_task"] = asyncio.current_task()
        self._cm = anyio.create_task_group()
        self._tg = await self._cm.__aenter__()
        self._tg.start_soon(anyio.sleep_forever)
        return ("read", "write")

    async def __aexit__(self, *exc_info):
        self._tg.cancel_scope.cancel()
        return await self._cm.__aexit__(*exc_info)


class _FakeClientSession:
    """``ClientSession`` 的最小替身：只提供 ``initialize()``。"""

    def __init__(self) -> None:
        self.value = SimpleNamespace(initialize=AsyncMock())

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *exc_info):
        return False


@contextlib.contextmanager
def _faked_transport_layer():
    """伪造传输层外部依赖，只保留 ``McpSseSession.connect()`` 自身逻辑。"""
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch.object(
                McpSseSession, "_tcp_endpoint_reachable", AsyncMock(return_value=True)
            )
        )
        stack.enter_context(
            patch(
                "app.services.ai.tools.mcp_client.streamable_http_client",
                lambda *args, **kwargs: _FakeTaskGroupTransport(),
            )
        )
        stack.enter_context(
            patch(
                "app.services.ai.tools.mcp_client.ClientSession",
                lambda *args, **kwargs: _FakeClientSession(),
            )
        )
        # 审计落库依赖基础设施；本用例只关心请求收尾，不是被测对象
        stack.enter_context(
            patch("app.core.middleware.AuditService.log_request_data", AsyncMock())
        )
        yield


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.post("/api/_test/mcp-session")
    async def _create_session():
        _OBSERVED["request_task"] = asyncio.current_task()
        if SERVER_ID not in _SESSIONS:
            session = McpSseSession(SERVER_ID, MCP_URL, {"X-Test": "1"})
            await session.connect()
            _SESSIONS[SERVER_ID] = session
        return {"ok": True}

    return app


async def _post_twice(app: FastAPI):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/_test/mcp-session")
        second = await client.post("/api/_test/mcp-session")
    return first, second


@pytest.fixture(autouse=True)
def _isolated_session_cache():
    _OBSERVED.clear()
    _SESSIONS.clear()
    yield
    _SESSIONS.clear()


@pytest.mark.asyncio
async def test_shared_mcp_session_transport_runs_in_its_own_task():
    with _faked_transport_layer():
        first, second = await _post_twice(_build_app())

        assert first.status_code == 200
        assert second.status_code == 200
        # 不变式本体：建链（anyio task group）不能发生在请求 task 里
        assert _OBSERVED["transport_task"] is not _OBSERVED["request_task"], (
            "MCP 传输层必须在专属 owner task 里建立；在请求 task 里建立会把 anyio "
            "cancel scope 留在请求栈上，请求收尾时抛 "
            "'Attempted to exit a cancel scope that isn't the current tasks's "
            "current cancel scope'"
        )

        session = _SESSIONS[SERVER_ID]
        assert session.is_direct_http is False
        assert session.session is not None
        # 共享会话仍在缓存里可用，owner task 常驻（下一行会随 close 收尾）
        assert session._owner_task is not None
        assert not session._owner_task.done()

        await session.close()


@pytest.mark.asyncio
async def test_close_tears_transport_down_in_owner_task():
    """关链也不能跨 task：close() 之后 owner 退出、栈清空、状态复位。"""
    with _faked_transport_layer():
        await _post_twice(_build_app())
        session = _SESSIONS[SERVER_ID]
        owner = session._owner_task

        await session.close()

        assert owner.done(), "close() 必须等 owner task 收尾（它负责退出 cancel scope）"
        assert session._exit_stack is None
        assert session.session is None
        assert session._transport_ready is False
        assert session._owner_task is None
