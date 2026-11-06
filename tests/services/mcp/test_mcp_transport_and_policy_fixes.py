"""出站链路与入站策略的一批加固回归。

覆盖外部 review 核实成立的几条：
1. 传输断开判定必须认识 httpx2 / anyio / ExceptionGroup（否则远端重启后不自愈）；
2. 单次 tools/call 超时不能被当成断连，否则写操作会被重发（远端执行两次）；
3. 无状态 Direct HTTP 网关的探测与 initialize 只做一次，空闲也能回收；
4. 远端 inputSchema 自带 x-nanzi-* 时必须剥掉；
5. 用户断言的自定义属性按子串过滤凭据，且坏 JSON 可降级；
6. IPv4-mapped IPv6 不能绕过云元数据拦截；
7. 远端参数名不合法（_id / $ref / 类关键字）不能把工具创建整条打死。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import anyio
import httpx
import httpx2
import pytest

from app.services.ai.tools import mcp_client as client_mod
from app.services.ai.tools.mcp_client import (
    McpClientService,
    McpSseSession,
    McpToolCallTimeout,
    _is_transport_error,
    _strip_platform_schema_extensions,
)
from app.services.ai.tools.mcp_factory import _safe_field_name
from app.services.mcp.url_policy import McpUrlPolicyError, validate_mcp_outbound_url


# --------------------------------------------------------------------------- #
# 1. 传输断开判定
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "err",
    [
        httpx.ConnectError("boom"),
        httpx2.ConnectError("boom"),
        httpx2.ReadTimeout("boom"),
        anyio.ClosedResourceError(),
        anyio.BrokenResourceError(),
        anyio.EndOfStream(),
        ConnectionResetError(),
        TimeoutError("socket timeout"),
    ],
)
def test_transport_errors_are_recognized(err):
    assert _is_transport_error(err) is True


def test_exception_group_is_recognized_when_any_member_is_transport():
    grouped = BaseExceptionGroup("anyio task group", [httpx2.ConnectError("inner")])
    assert _is_transport_error(grouped) is True


def test_business_timeout_is_not_a_transport_error():
    """超时是「远端太慢」，不是「连接断了」：重连重发会把写操作执行两遍。"""
    assert _is_transport_error(McpToolCallTimeout("slow tool")) is False
    # 对外仍是 TimeoutError 语义，调用方无需改判
    assert isinstance(McpToolCallTimeout("x"), TimeoutError)


async def test_call_tool_timeout_raises_dedicated_type_without_retry(monkeypatch):
    """``_call_tool_with_progress`` 超时后抛专用类型，且不再被重连路径接住。"""
    session = SimpleNamespace(call_tool=None)

    async def _forever(*args, **kwargs):
        await anyio.sleep(5)

    session.call_tool = _forever
    with pytest.raises(McpToolCallTimeout):
        await client_mod._call_tool_with_progress(session, "write_row", {}, timeout_seconds=0.01)


# --------------------------------------------------------------------------- #
# 3. 无状态 Direct HTTP：探测与 initialize 只做一次
# --------------------------------------------------------------------------- #
async def test_connect_short_circuits_once_direct_http_is_ready(monkeypatch):
    session = McpSseSession("srv-1", "https://gw.example.com/mcp", {})
    session._transport_ready = True
    session.is_direct_http = True

    calls = {"probe": 0}

    async def _probe():
        calls["probe"] += 1
        return True

    monkeypatch.setattr(session, "_tcp_endpoint_reachable", _probe)
    await session.connect()
    assert calls["probe"] == 0, "已确定传输可用时不应再重跑整套探测"


async def test_direct_http_initialize_runs_once_for_stateless_gateway(monkeypatch):
    session = McpSseSession("srv-2", "https://gw.example.com/mcp", {})
    session.is_direct_http = True
    seen = []

    async def _fake_rpc(_mgr, method, params, **kwargs):
        seen.append(method)
        return {"ok": True}

    monkeypatch.setattr(McpClientService, "_direct_http_rpc", _fake_rpc)

    await McpClientService._ensure_direct_http_initialized(session)
    await McpClientService._ensure_direct_http_initialized(session)
    await McpClientService._ensure_direct_http_initialized(session)

    assert seen.count("initialize") == 1, seen
    assert seen.count("notifications/initialized") == 1, seen


async def test_idle_cleanup_evicts_stateless_direct_http_session(monkeypatch):
    """无状态会话 session/session_id 都是 None，旧判据会让它永远留在字典里。"""
    stale = McpSseSession("srv-3", "https://gw.example.com/mcp", {})
    stale.is_direct_http = True
    stale._transport_ready = True
    stale.last_used_at = 0

    closed = {"count": 0}

    async def _close():
        closed["count"] += 1

    monkeypatch.setattr(stale, "close", _close)
    sessions = {"srv-3": stale}
    monkeypatch.setattr(McpClientService, "_sessions", sessions)

    # 第二轮 sleep 直接退出循环，避免依赖真实 60s 间隔
    rounds = {"n": 0}

    async def _sleep(_seconds):
        rounds["n"] += 1
        if rounds["n"] > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(client_mod.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await McpClientService._idle_cleanup_loop()

    assert closed["count"] == 1
    assert "srv-3" not in sessions


# --------------------------------------------------------------------------- #
# 4. 远端 schema 不能带平台私有扩展
# --------------------------------------------------------------------------- #
def test_strip_platform_schema_extensions_is_recursive():
    raw = {
        "type": "object",
        "x-nanzi-evidence-policy": {"mode": "none"},
        "properties": {
            "q": {"type": "string", "x-nanzi-evidence-types": ["doc"]},
        },
        "required": ["q"],
    }
    cleaned = _strip_platform_schema_extensions(raw)
    assert "x-nanzi-evidence-policy" not in cleaned
    assert "x-nanzi-evidence-types" not in cleaned["properties"]["q"]
    assert cleaned["properties"]["q"]["type"] == "string"
    assert cleaned["required"] == ["q"]


# --------------------------------------------------------------------------- #
# 7. 参数名兼容
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("_id", "id"),
        ("$ref", "ref"),
        ("2fa", "field_2fa"),
        ("class", "class_"),
        ("model_config", "field_model_config"),
        ("model_fields", "field_model_fields"),
        ("", "value"),
        ("ok_name", "ok_name"),
    ],
)
def test_safe_field_name(raw, expected):
    assert _safe_field_name(raw) == expected


def test_create_tool_survives_illegal_param_names():
    from app.services.ai.tools.mcp_factory import McpToolFactory

    record = SimpleNamespace(
        id="t1",
        server_id="s1",
        tool_name="demo:weird",
        tool_description="demo",
        parameter_schema=json.dumps(
            {
                "type": "object",
                "properties": {
                    "_id": {"type": "string"},
                    "$ref": {"type": "string"},
                    "2fa": {"type": "boolean"},
                    "class": {"type": "string"},
                    "model_config": {"type": "string"},
                },
                "required": ["_id"],
            }
        ),
        is_published=True,
        is_available=True,
    )
    tool = McpToolFactory.create_tool(record)
    assert set(tool.args_schema.model_fields) == {
        "id",
        "ref",
        "field_2fa",
        "class_",
        "field_model_config",
    }


# --------------------------------------------------------------------------- #
# 6. IPv4-mapped IPv6
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:100.100.100.200]/latest/meta-data/",
        "http://[::ffff:168.63.129.16]/x",
        "http://[::ffff:169.254.169.254]/latest/meta-data/",
        "http://100.100.100.200/x",
    ],
)
def test_metadata_ips_are_blocked_in_mapped_form(url):
    with pytest.raises(McpUrlPolicyError):
        validate_mcp_outbound_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://[::ffff:127.0.0.1]:8080/mcp", "http://127.0.0.1:8080/mcp", "http://10.0.0.5/mcp"],
)
def test_private_and_loopback_stay_allowed(url):
    assert validate_mcp_outbound_url(url) == url


# --------------------------------------------------------------------------- #
# 8. 沙箱内联 MCP 路径边界 & OAuth 同源校验 & 取消审计状态
# --------------------------------------------------------------------------- #
def test_inline_sandbox_mcp_rejects_sibling_prefix_and_glob_escape():
    from app.services.ai.runtime.agentscope import workspace_container_mcp as mcp_mod

    code = mcp_mod._INLINE_SERVER.replace('mcp.run(transport="stdio")', "")

    class _FakeMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            return lambda fn: fn

    ns: dict = {"__name__": "test_inline", "FastMCP": _FakeMCP}
    exec(compile(code, "<inline>", "exec"), ns)  # noqa: S102
    abspath = ns["_abspath"]
    glob_fn = ns["glob"]

    assert abspath("sub/a.py") == "/workspace/sub/a.py"
    with pytest.raises(ValueError, match="path escapes workspace"):
        abspath("../workspace_other/secret.txt")
    with pytest.raises(ValueError, match="path escapes workspace"):
        glob_fn("/etc/*")
    with pytest.raises(ValueError, match="path escapes workspace"):
        glob_fn("../../etc/*")


def test_same_site_submission_rejects_null_origin(monkeypatch):
    from app.api import mcp_platform

    monkeypatch.setattr(mcp_platform, "_base_url", lambda: "https://mcp.example.com")
    req_null = SimpleNamespace(headers={"origin": "null"})
    req_evil = SimpleNamespace(headers={"origin": "https://evil.example.com"})
    req_ok = SimpleNamespace(headers={"origin": "https://mcp.example.com"})
    assert mcp_platform._same_site_submission(req_null) is False
    assert mcp_platform._same_site_submission(req_evil) is False
    assert mcp_platform._same_site_submission(req_ok) is True

