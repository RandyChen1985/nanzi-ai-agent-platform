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


@pytest.mark.asyncio
async def test_script_render_and_preview_redact_before_truncation(tmp_path, monkeypatch):
    """脚本工具渲染与 /script/preview 必须先脱敏再截断，并透传试跑 user_info。"""
    import json

    from app.api.portal.endpoints.script_tools import preview_script_tool
    from app.schemas.script_tool import SysScriptToolPreviewRequest
    from app.services.ai.tools.script_tool_runtime import (
        ScriptRunResult,
        ScriptToolConfig,
        ScriptToolRunner,
        render_result_for_model,
    )

    # 1. 横跨 100KB 边界的纯文本 stdout 必须整体脱敏，不能只抹后面或漏前半段明文
    padding = "A" * (100 * 1024 - 10)
    res_plain = ScriptRunResult(
        ok=True, exit_code=0, stdout=f"{padding}{SECRET}", stderr="", duration_ms=10
    )
    rendered_plain = await render_result_for_model(res_plain, workdir=tmp_path)
    assert SECRET[:10] not in rendered_plain

    # 2. 失败分支的 stderr / stdout 同理先脱敏再截断
    res_err = ScriptRunResult(
        ok=False, exit_code=2, stdout=f"out={SECRET}", stderr=f"{padding}{SECRET}", duration_ms=10
    )
    rendered_err = await render_result_for_model(res_err, workdir=tmp_path)
    assert SECRET[:10] not in rendered_err

    # 3. 带转义双引号的 JSON stdout 也不能穿透
    tricky_secret = 'sk-quote-"12345678'
    register_injected(tricky_secret)
    res_json = ScriptRunResult(
        ok=True,
        exit_code=0,
        stdout=json.dumps({"token": tricky_secret}, ensure_ascii=False),
        stderr="",
        duration_ms=10,
    )
    rendered_json = await render_result_for_model(res_json, workdir=tmp_path)
    assert "12345678" not in rendered_json

    # 4. ScriptToolRunner 试跑优先使用传入 user_info 的属性，并隔离上下文其它用户
    from app.utils.context import current_user_info

    tok = current_user_info.set({"user_id": 100, "email": "ctx@corp.cn", "dept_code": "OLD"})
    try:
        cfg = ScriptToolConfig(
            name="demo_script",
            code="print('ok')",
            parameter_schema={
                "type": "object",
                "properties": {
                    "who": {"type": "string", "default": "{user.email}"},
                },
                "required": ["who"],
            },
        )
        runner = ScriptToolRunner(
            cfg, workdir=tmp_path, user_info={"user_id": 200, "email": "trial@corp.cn"}
        )
        assert runner.prepare_params({})["who"] == "trial@corp.cn"
    finally:
        current_user_info.reset(tok)

    # 5. /script/preview 返回跨 20000/8000 截断边界与异常消息也必须已脱敏
    preview_padding = "B" * 19995
    fake_result = ScriptRunResult(
        ok=True,
        exit_code=0,
        stdout=f"{preview_padding}{SECRET}",
        stderr=f"{'E' * 7995}{SECRET}",
        duration_ms=12,
        message=f"secret={SECRET}",
    )

    monkeypatch.setattr(
        "app.api.portal.endpoints.script_tools.current_sandbox_policy",
        lambda: _async_val("local"),
    )
    monkeypatch.setattr(
        "app.api.portal.endpoints.script_tools._preview_workdir",
        lambda user_info: _async_val(tmp_path),
    )
    monkeypatch.setattr(
        "app.api.portal.endpoints.script_tools.ScriptToolRunner.run",
        lambda self, params: _async_val(fake_result),
    )
    req = SysScriptToolPreviewRequest(code="print('ok')", language="python")
    resp = await preview_script_tool(req, db=None, user={"user_id": 200})
    assert SECRET[:10] not in resp.stdout
    assert SECRET[:10] not in resp.stderr
    assert SECRET not in (resp.message or "")


async def _async_val(val):
    return val


@pytest.mark.asyncio
async def test_generic_api_info_log_redacts_url_and_params(caplog, monkeypatch):
    """GenericApiToolFactory._perform_request 的 INFO 日志对注入后 URL 与参数脱敏。"""
    import logging
    import httpx

    from app.services.ai.tools import generic_api
    from app.services.ai.tools.generic_api import GenericApiToolFactory

    class _FakeResp:
        status_code = 200
        text = '{"ok": true}'

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN002
            return False

        async def request(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(generic_api, "credential_param_names", lambda s: {"token_in_url"})

    async def _fake_param_bind(config, params, **kwargs):  # noqa: ANN001
        return {**params, "token_in_url": SECRET, "token_in_query": SECRET}

    monkeypatch.setattr(generic_api, "apply_api_tool_param_bindings", _fake_param_bind)

    config = SimpleNamespace(
        id="",
        name="log_check_api",
        method="GET",
        url_template="https://api.example.com/{token_in_url}/items",
        headers="{}",
        parameter_schema="{}",
        signing_enabled=False,
        signing_algorithm=None,
        signing_key_id=None,
        signing_secret_encrypted=None,
    )

    caplog.set_level(logging.INFO)
    await GenericApiToolFactory._perform_request(config, {}, user_info={})
    assert SECRET not in caplog.text
    assert "Executing log_check_api" in caplog.text


@pytest.mark.asyncio
async def test_mcp_stream_collects_injected_values_from_child_task(monkeypatch):
    """stream_remote_tool_with_progress 通过 copy_context 将子协程新注册凭据合并回父协程。"""
    from app.services.ai.tools.mcp_client import McpClientService
    from app.services.ai.tools.mcp_factory import stream_remote_tool_with_progress
    from app.services.secrets.redact import injected_values, redact_text

    child_secret = "sk-mcp-child-only-token-887766"

    async def _fake_call_remote_tool(**kwargs):  # noqa: ANN003
        register_injected(child_secret)
        return "done"

    monkeypatch.setattr(McpClientService, "call_remote_tool", _fake_call_remote_tool)

    items = [
        item
        async for item in stream_remote_tool_with_progress(
            server_id="srv-1",
            tool_name="t1",
            arguments={},
        )
    ]
    assert items == ["done"]
    assert child_secret in injected_values()
    assert child_secret not in redact_text(f"err={child_secret}")


@pytest.mark.asyncio
async def test_mcp_direct_http_request_error_strips_raw_exception(caplog):
    """Direct HTTP 网络异常包装新异常并清空 __cause__，不回显底层 URL 凭据。"""
    import asyncio
    import logging

    from app.services.ai.tools.mcp_client import McpClientService

    class _FailingClient:
        async def post(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError(f"ConnectError https://mcp.local/rpc?api_key={SECRET}")

    mgr = SimpleNamespace(
        auth_headers={},
        mcp_session_id=None,
        sse_url="https://mcp.local/rpc",
        server_id="srv-1",
        next_rpc_id=lambda: 1,
        _active_requests_changed=asyncio.Event(),
        get_http_client=lambda: _FailingClient(),
    )
    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError) as exc_info:
        await McpClientService._direct_http_rpc(mgr, "tools/call", {})

    assert exc_info.value.__cause__ is None
    assert SECRET not in str(exc_info.value)
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_outbound_audit_redacts_current_context_even_with_empty_storage_scanner(
    monkeypatch,
):
    """多 worker 存储快照缓存未刷新时，出站审计仍能利用当前 ContextVar 脱敏。"""
    from app.services.mcp import outbound_audit

    captured: list = []

    class _EmptyScanner:
        def mask_data(self, payload):  # noqa: ANN001
            return payload

        def mask_text(self, text):  # noqa: ANN001
            return text

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN002
            return False

        async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return SimpleNamespace(scalar_one_or_none=lambda: "srv-name")

        def add(self, entry):  # noqa: ANN001
            captured.append(entry)

        async def commit(self):
            return None

    monkeypatch.setattr(outbound_audit, "AsyncSessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        outbound_audit, "build_storage_scanner", lambda: _async_val(_EmptyScanner())
    )

    await outbound_audit.record_outbound_audit_log(
        server_id="srv-1",
        tool_name="t1",
        arguments={"token_payload": SECRET},
        tool_output={"echo": SECRET},
        error_message=f"failed bearer {SECRET}",
        status="error",
    )

    assert len(captured) == 1
    row = captured[0]
    assert SECRET not in str(row.tool_input)
    assert SECRET not in str(row.tool_output)
    assert SECRET not in (row.error_message or "")
