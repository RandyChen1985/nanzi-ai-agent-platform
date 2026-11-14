"""MCP 会话不能跨用户复用：一旦绑了个人凭据，必须按用户隔离。

SSE 的 Header 在**建连那一刻**冻结——``get_session`` 命中缓存时直接忽略新传入
的 ``auth_headers``（见 ``cache_key not in cls._sessions`` 那个分支）。而默认
``session_key`` 就是 ``server_id``，也就是全平台共用一个连接。

后果：第一个人的凭据会被后面所有人用着。更糟的是回收只在**空闲** 300 秒后
发生，所以只要调用不断，这个连接永不回收，串号没有时间上限。

``user_assertion_enabled``（签名断言）那条路早就做了每用户临时会话，但「用户自己
绑了凭据」这条路没做——恰恰是凭据中心最需要隔离的场景。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.tools.mcp_client import McpClientService


pytestmark = pytest.mark.no_infrastructure


def _server(**overrides):
    base = {
        "id": "server-1",
        "server_name": "demo",
        "sse_url": "https://mcp.example.com/sse",
        "enabled_status": 1,
        # 默认不开签名断言，走的是「用户绑了自己的凭据」这条路
        "user_assertion_enabled": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _session_mgr():
    return SimpleNamespace(
        is_direct_http=False,
        session=SimpleNamespace(
            call_tool=AsyncMock(return_value=SimpleNamespace(isError=False, content=[]))
        ),
        close=AsyncMock(),
    )


def _patched(monkeypatch, *, bound_user_of_headers: bool):
    """装好「凭据注入被调用」的环境，并记录每次建连用的 session_key。

    ``bound_user_of_headers`` 为 False 时模拟「这个服务没有任何绑定」，用来
    确认隔离改造没有把普通服务的连接复用也一起牺牲掉。
    """
    server = _server()
    session_mgr = _session_mgr()
    calls: list[tuple[str, dict]] = []

    async def fake_apply(server, headers, *, user_info=None, denials=None, used=None):
        if not bound_user_of_headers:
            return dict(headers)
        user_id = str((user_info or {}).get("user_id") or "")
        if used is not None:
            used["user_id"] = user_id
            used["slots"] = ["header:X-Tenant"]
        return {**headers, "X-Tenant": f"tenant-of-{user_id}"}

    async def fake_get_session(server_id, *, session_key=None, auth_headers=None, server_snapshot=None):
        if bound_user_of_headers:
            assert server_snapshot is server
        calls.append((session_key, dict(auth_headers or {})))
        return session_mgr

    monkeypatch.setattr(McpClientService, "_load_server", AsyncMock(return_value=server))
    monkeypatch.setattr(
        "app.services.ai.tools.mcp_client.build_mcp_headers",
        lambda *args, **kwargs: {"Accept": "application/json"},
    )
    monkeypatch.setattr(
        "app.services.ai.tools.mcp_client.apply_mcp_bindings", fake_apply
    )
    monkeypatch.setattr(McpClientService, "get_session", fake_get_session)
    return server, session_mgr, calls


@pytest.mark.asyncio
async def test_two_users_never_share_a_session_when_credentials_are_bound(monkeypatch):
    _, session_mgr, calls = _patched(monkeypatch, bound_user_of_headers=True)

    await McpClientService.call_remote_tool(
        "server-1", "get-data", {}, user_info={"user_id": "101"}
    )
    await McpClientService.call_remote_tool(
        "server-1", "get-data", {}, user_info={"user_id": "202"}
    )

    first_key, first_headers = calls[0]
    second_key, second_headers = calls[1]

    # 两个人绝不能落在同一个缓存 key 上——否则第二个人拿到的就是第一个人建连时的 Header
    assert first_key != second_key
    assert first_key.startswith("server-1:user:101:bind:")
    assert second_key.startswith("server-1:user:202:bind:")
    assert first_headers["X-Tenant"] == "tenant-of-101"
    assert second_headers["X-Tenant"] == "tenant-of-202"

    # 用完必须销毁：留着就是下一个人复用
    assert session_mgr.close.await_count == 2


@pytest.mark.asyncio
async def test_same_user_gets_a_fresh_session_per_call(monkeypatch):
    """同一个人换过凭据值后，不能让旧连接继续顶着他的身份发请求。"""
    _, _, calls = _patched(monkeypatch, bound_user_of_headers=True)

    await McpClientService.call_remote_tool(
        "server-1", "get-data", {}, user_info={"user_id": "101"}
    )
    await McpClientService.call_remote_tool(
        "server-1", "get-data", {}, user_info={"user_id": "101"}
    )

    assert calls[0][0] != calls[1][0], "同一用户两次调用也不能复用带旧凭据的连接"


@pytest.mark.asyncio
async def test_service_without_bindings_keeps_the_shared_session(monkeypatch):
    """没绑凭据的服务保持原来的共享会话：不能为了隔离把连接复用一刀砍掉。"""
    _, session_mgr, calls = _patched(monkeypatch, bound_user_of_headers=False)

    await McpClientService.call_remote_tool(
        "server-1", "get-data", {}, user_info={"user_id": "101"}
    )
    await McpClientService.call_remote_tool(
        "server-1", "get-data", {}, user_info={"user_id": "202"}
    )

    assert [key for key, _ in calls] == ["server-1", "server-1"]
    session_mgr.close.assert_not_awaited()
