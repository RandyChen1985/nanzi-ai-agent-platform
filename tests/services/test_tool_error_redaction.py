"""工具报错回显必须脱敏：远端会把凭据原样回显在错误体里。

背景（实锤 bug）：API 工具与 MCP 客户端的**正常返回**都走了 ``redact_value``，
但异常分支漏了。而 401/400 的响应体恰恰是最容易回显请求头的地方：

    HTTP 401: {"error": "invalid token: Bearer sk-live-xxxxx"}

不脱敏就等于把刚注入的凭据明文交给模型，并随会话轨迹写进历史表 ——
用户在界面上翻历史就能看到自己的密钥。

本文件钉住两条链路：
1. ``GenericApiToolFactory`` 抛 HTTP 错误与兜底异常；
2. MCP 的 ``isError`` 与直连 HTTP 报错。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.secrets.redact import register_injected, reset_injected

SECRET = "sk-live-9f3a7c1e5b2d"


@pytest.fixture(autouse=True)
def _registered_secret():
    """脱敏依赖「已注入值」登记表，测试里显式登记一条。"""
    reset_injected()
    register_injected(SECRET)
    try:
        yield
    finally:
        reset_injected()


@pytest.mark.asyncio
async def test_api_tool_http_error_body_is_redacted(monkeypatch):
    from app.services.ai.tools.generic_api import GenericApiToolFactory

    async def fake_perform(config, params, **kwargs):  # noqa: ANN001
        raise RuntimeError(
            'HTTP 401: {"error": "invalid token: Bearer %s"}' % SECRET
        )

    monkeypatch.setattr(
        GenericApiToolFactory, "_perform_request", staticmethod(fake_perform)
    )

    result = await GenericApiToolFactory._execute_request(
        SimpleNamespace(name="天气查询工具"), {}
    )

    assert SECRET not in result
    assert "HTTP 401" in result


@pytest.mark.asyncio
async def test_api_tool_unexpected_exception_is_redacted(monkeypatch):
    """兜底分支同样要脱敏：底层异常可能带着完整 URL 与 headers。"""
    from app.services.ai.tools.generic_api import GenericApiToolFactory

    async def fake_perform(config, params, **kwargs):  # noqa: ANN001
        raise ValueError("connect failed to https://api.example.com?token=%s" % SECRET)

    monkeypatch.setattr(
        GenericApiToolFactory, "_perform_request", staticmethod(fake_perform)
    )

    result = await GenericApiToolFactory._execute_request(
        SimpleNamespace(name="天气查询工具"), {}
    )

    assert SECRET not in result
    assert "Execution Error" in result


def test_redact_helper_masks_registered_secret():
    """前置确认：登记过的值确实会被掩码，否则上面两条断言会假通过。"""
    from app.services.secrets.redact import redact_value

    masked = redact_value("token=%s" % SECRET, where="test")
    assert SECRET not in str(masked)


@pytest.mark.asyncio
async def test_api_tool_http_error_redacts_before_500_slice(monkeypatch):
    """非 2xx 响应体须先脱敏再做 [:500] 截断，避免密钥跨边界被腰斩后泄露前半段明文。"""
    import httpx
    from app.services.ai.tools.generic_api import GenericApiToolFactory

    body_crossing_500 = ("a" * 490) + SECRET
    fake_resp = httpx.Response(
        status_code=401,
        text=body_crossing_500,
        request=httpx.Request("GET", "https://api.example.com/v1"),
    )

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        async def get(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return fake_resp

        async def request(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return fake_resp

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    config = SimpleNamespace(
        id="",
        name="test_api",
        method="GET",
        url_template="https://api.example.com/v1",
        headers="{}",
        parameter_schema="{}",
        signing_enabled=False,
        signing_algorithm=None,
        signing_key_id=None,
        signing_secret_encrypted=None,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await GenericApiToolFactory._perform_request(config, {}, user_info={})

    err = str(exc_info.value)
    assert SECRET not in err
    assert SECRET[:10] not in err


# --------------------------------------------------------------------------- #
# MCP：正常返回同样是脱敏对象
#
# 上面两条只覆盖了 GenericApiToolFactory，mcp_client 一条都没有 —— 结果本轮
# 「错误分支补脱敏」时把 _call_direct_http_tool 的三个**正常返回**全漏了。
# 正常返回才是 MCP 调用的主路径，补上，防止再漏。
# --------------------------------------------------------------------------- #


def test_mcp_direct_http_content_is_redacted():
    """直连 HTTP 工具的正常返回必须脱敏（远端会回显入参）。"""
    from app.services.ai.tools.mcp_client import McpClientService

    # 走 res 为 dict + content 的分支
    res = {
        "content": [{"type": "text", "text": "echo: token=%s" % SECRET}],
    }
    result = McpClientService._normalize_direct_http_tool_result(res, "echo_tool")

    assert SECRET not in str(result)


def test_mcp_direct_http_structured_content_is_redacted():
    from app.services.ai.tools.mcp_client import McpClientService

    res = {
        "content": [{"type": "text", "text": "ok"}],
        "structuredContent": {"echo": {"auth": SECRET}},
    }
    result = McpClientService._normalize_direct_http_tool_result(res, "echo_tool")

    assert SECRET not in str(result)


def test_mcp_direct_http_raw_body_is_redacted():
    """非 content 结构时整段串化返回，也必须脱敏。"""
    from app.services.ai.tools.mcp_client import McpClientService

    result = McpClientService._normalize_direct_http_tool_result(
        {"any": "token=%s" % SECRET}, "echo_tool"
    )

    assert SECRET not in str(result)


def test_mcp_sse_is_error_helper_is_redacted():
    """SSE 正常与 isError 共用 _normalize_sse_tool_result，错误体必须脱敏。"""
    from app.services.ai.tools.mcp_client import McpClientService

    resp = SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text="upstream 401: Bearer %s" % SECRET)],
        structuredContent=None,
    )
    with pytest.raises(RuntimeError) as exc_info:
        McpClientService._normalize_sse_tool_result(resp, "echo_tool")

    assert SECRET not in str(exc_info.value)


@pytest.mark.asyncio
async def test_mcp_sse_reconnect_is_error_and_audit_are_redacted(monkeypatch):
    """SSE 断连重连后的 isError 与外层异常、出站审计都不得出现明文凭据。"""
    from unittest.mock import AsyncMock

    from app.services.ai.tools import mcp_client
    from app.services.ai.tools.mcp_client import McpClientService

    class _FlakySession:
        def __init__(self) -> None:
            self.attempts = 0

        async def call_tool(self, name: str, arguments: dict):  # noqa: ANN001
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("eof https://api.example.com?token=%s" % SECRET)
            return SimpleNamespace(
                isError=True,
                content=[SimpleNamespace(text="invalid header Bearer %s" % SECRET)],
                structuredContent=None,
            )

    class _Manager:
        server_id = "srv-1"
        is_direct_http = False

        def __init__(self) -> None:
            self.session = _FlakySession()

        async def close(self) -> None:
            return None

        async def connect(self) -> None:
            return None

    manager = _Manager()
    audit_mock = AsyncMock()
    monkeypatch.setattr(McpClientService, "get_session", AsyncMock(return_value=manager))
    monkeypatch.setattr(mcp_client, "record_outbound_audit_log", audit_mock)

    with pytest.raises(RuntimeError) as exc_info:
        await McpClientService.call_remote_tool("srv-1", "echo_tool", {})

    import asyncio

    await asyncio.sleep(0)
    assert manager.session.attempts == 2
    assert SECRET not in str(exc_info.value)
    await_calls = audit_mock.await_args_list
    assert len(await_calls) == 1
    assert SECRET not in str(await_calls[0].kwargs.get("error_message") or "")


@pytest.mark.asyncio
async def test_mcp_direct_http_rpc_error_and_non_2xx_are_redacted(caplog):
    """Direct HTTP 的 JSON-RPC error 与非 2xx body 在异常与日志中都要脱敏。"""
    import asyncio
    import json
    import logging

    from app.services.ai.tools.mcp_client import McpClientService

    class _FakeResp:
        def __init__(self, status_code: int, text: str) -> None:
            self.status_code = status_code
            self.text = text
            self.headers = {}

        def json(self):
            return json.loads(self.text)

    class _FakeClient:
        def __init__(self, resp: _FakeResp) -> None:
            self._resp = resp

        async def post(self, *args, **kwargs):  # noqa: ANN001
            return self._resp

    def _mgr(resp: _FakeResp):
        return SimpleNamespace(
            auth_headers={},
            mcp_session_id=None,
            sse_url="https://mcp.local/rpc",
            server_id="srv-1",
            next_rpc_id=lambda: 1,
            _active_requests_changed=asyncio.Event(),
            get_http_client=lambda: _FakeClient(resp),
        )

    caplog.set_level(logging.ERROR)

    rpc_err_resp = _FakeResp(
        200,
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "bad token Bearer %s" % SECRET},
            }
        ),
    )
    with pytest.raises(Exception) as rpc_exc:
        await McpClientService._direct_http_rpc(_mgr(rpc_err_resp), "tools/call", {})
    assert "RPC Error -32000" in str(rpc_exc.value)
    assert SECRET not in str(rpc_exc.value)

    http_err_resp = _FakeResp(401, '{"error": "invalid token: Bearer %s"}' % SECRET)
    with pytest.raises(Exception) as http_exc:
        await McpClientService._direct_http_rpc(_mgr(http_err_resp), "tools/call", {})
    assert "HTTP 401" in str(http_exc.value)
    assert SECRET not in str(http_exc.value)
    assert SECRET not in caplog.text
