"""热刷新探测的派发位置契约（回归锁）。

这里锁的是一次真实事故：``_dispatch_hot_probe`` 的调用点最初被插进了
``list_remote_tools`` 的 ``finally``，而那个函数里根本没有 ``call_status``
这个变量——于是**每一次成功的工具同步都会在 finally 抛 NameError**，
把「已经拉到的工具列表」整个丢掉。表现是 MCP 管理页点同步必失败、
创建服务时自动同步必失败、定义探测把所有服务记成 unreachable。

为什么既有测试全绿却没抓到：所有涉及同步/探测的用例都把
``list_remote_tools`` 整个 mock 掉了，恰恰绕开了出问题的那几行。所以这组
用例**不 mock 传输层**，用假 session 真跑这两个函数的 finally。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

import app.services.ai.tools.mcp_client as mcp_client_module
from app.services.ai.tools.mcp_client import McpClientService


class _FakeSessionMgr:
    """够用的会话替身：direct-http 分支只用到 is_direct_http / close()。"""

    is_direct_http = True
    session = None

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_transport(monkeypatch):
    """把传输层换成假的，但保留 list_remote_tools / call_remote_tool 的真实函数体。"""
    state: Dict[str, Any] = {"dispatched": [], "call_error": None}

    async def _get_session(cls, server_id, **kwargs):  # noqa: ANN001
        return _FakeSessionMgr()

    async def _list_tools(cls, session_mgr):  # noqa: ANN001
        return []

    async def _call_tool(cls, session_mgr, tool_name, arguments, timeout_seconds=None):  # noqa: ANN001
        if state["call_error"] is not None:
            raise state["call_error"]
        return {"ok": True}

    async def _noop_audit(**kwargs):  # noqa: ANN003
        return None

    async def _load_server(cls, server_id):  # noqa: ANN001
        # 只提供 call_remote_tool 真正会读的几个字段：请求头构造本身不属于本组关注点
        return SimpleNamespace(
            server_name="demo",
            enabled_status=1,
            user_assertion_enabled=False,
            credential_mode="static",
            auth_headers=None,
            fixed_token_encrypted=None,
            id=server_id,
        )

    def _fake_build_headers(server, **kwargs):  # noqa: ANN001, ANN003
        return {}

    async def _fake_apply_bindings(server, headers, **kwargs):  # noqa: ANN001, ANN003
        return dict(headers or {})

    def _record_dispatch(server_id, user_info):  # noqa: ANN001
        state["dispatched"].append((server_id, user_info))

    monkeypatch.setattr(McpClientService, "_load_server", classmethod(_load_server))
    monkeypatch.setattr(mcp_client_module, "build_mcp_headers", _fake_build_headers)
    monkeypatch.setattr(mcp_client_module, "apply_mcp_bindings", _fake_apply_bindings)
    monkeypatch.setattr(McpClientService, "get_session", classmethod(_get_session))
    monkeypatch.setattr(McpClientService, "_list_direct_http_tools", classmethod(_list_tools))
    monkeypatch.setattr(McpClientService, "_call_direct_http_tool", classmethod(_call_tool))
    monkeypatch.setattr(mcp_client_module, "record_outbound_audit_log", _noop_audit)
    monkeypatch.setattr(McpClientService, "_dispatch_hot_probe", staticmethod(_record_dispatch))
    return state


async def test_list_remote_tools_finishes_without_name_error(fake_transport) -> None:
    """拉工具列表必须能正常返回——它不该碰任何「调用状态」变量。"""
    tools = await McpClientService.list_remote_tools("dummy-server")
    assert tools == []


async def test_list_remote_tools_does_not_dispatch_hot_probe(fake_transport) -> None:
    """同步（tools/list）不是「调用成功」，不该触发热刷新探测。

    否则探测自己也会 list_remote_tools，形成「探一次再探一次」的自我触发。
    """
    await McpClientService.list_remote_tools("dummy-server")
    assert fake_transport["dispatched"] == []


async def test_successful_call_dispatches_hot_probe_once(fake_transport) -> None:
    """工具调用成功 → 派发一次热刷新（server_id 与操作者原样带过去）。"""
    user_info = {"user_id": "42"}
    await McpClientService.call_remote_tool(
        "dummy-server", "demo_tool", {}, user_info=user_info
    )
    # 派发是 create_task，让事件循环跑一轮再断言
    await asyncio.sleep(0)
    assert fake_transport["dispatched"] == [("dummy-server", user_info)]


async def test_failed_call_does_not_dispatch(fake_transport) -> None:
    """调用失败不派发：失败的服务往往连不上，再探一次只是浪费一次超时。"""
    fake_transport["call_error"] = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await McpClientService.call_remote_tool("dummy-server", "demo_tool", {})
    await asyncio.sleep(0)
    assert fake_transport["dispatched"] == []


async def test_timeout_is_reraised_and_not_dispatched(fake_transport) -> None:
    """超时要原样上抛（上层靠异常类型判「超时」），且不派发热刷新。"""
    fake_transport["call_error"] = TimeoutError()
    with pytest.raises(TimeoutError):
        await McpClientService.call_remote_tool("dummy-server", "demo_tool", {})
    await asyncio.sleep(0)
    assert fake_transport["dispatched"] == []
