"""`/dashboard/mcp` 深度 Review 修复项后端回归测试（Batch 1 P0 + Batch 2 P1）。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import httpx2
import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import mcp as mcp_endpoints
from app.models.mcp import McpServer, McpToolCache
from app.services import mcp_share_service as share_svc
from app.services.ai.tools import mcp_client as client_mod
from app.services.ai.tools.mcp_client import (
    McpClientService,
    McpSseSession,
    McpToolCallTimeout,
    _create_no_redirect_mcp_http_client,
    _is_transport_error,
)
from app.services.mcp import definition_diff, mcp_auth_policy, security_audit, tool_ref_health
from app.services.mcp.url_policy import McpUrlPolicyError, validate_mcp_outbound_url
from app.services.secrets.redact import redact_value, reset_injected
from app.utils.encryption import get_api_key_manager

pytestmark = pytest.mark.no_infrastructure


# ---------------------------------------------------------------------------
# 1. URL SSRF 策略：非标准 IPv4 与 NAT64 / IPv4-translated IPv6 拦截
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://2852039166/latest/meta-data/",  # 169.254.169.254 十进制整数
        "http://0xa9fea9fe/latest/meta-data/",  # 169.254.169.254 十六进制
        "http://0251.0376.0251.0376/latest/meta-data/",  # 169.254.169.254 八进制
        "http://169.254.43518/latest/meta-data/",  # 169.254.169.254 省略段
        "http://0x646464c8/latest/meta-data/",  # 100.100.100.200 阿里云元数据十六进制
        "http://0/mcp",  # 0.0.0.0 未指定地址
        "http://[64:ff9b::169.254.169.254]/mcp",  # NAT64 AWS/GCP 元数据
        "http://[64:ff9b::100.100.100.200]/mcp",  # NAT64 阿里云元数据
        "http://[::ffff:0:169.254.169.254]/mcp",  # IPv4-translated 元数据
    ],
)
def test_url_policy_blocks_non_standard_ip_and_nat64_metadata(url: str):
    with pytest.raises(McpUrlPolicyError):
        validate_mcp_outbound_url(url)


# ---------------------------------------------------------------------------
# 2. 认证 Header 校验、大小写去重、静态凭据脱敏池与 issuer 覆盖优先级
# ---------------------------------------------------------------------------

def test_parse_auth_headers_rejects_crlf_and_forbidden_headers():
    # 传输帧控制头必须拒（长度/分块语义被改写会导致请求走私）
    with pytest.raises(ValueError):
        mcp_auth_policy._parse_auth_headers({"Content-Length": "0"})
    with pytest.raises(ValueError):
        mcp_auth_policy._parse_auth_headers({"Transfer-Encoding": "chunked"})
    # 控制字符与非法头名必须拒
    with pytest.raises(ValueError):
        mcp_auth_policy._parse_auth_headers({"X-Custom": "ok\r\nX-Injected: bad"})
    with pytest.raises(ValueError):
        mcp_auth_policy._parse_auth_headers({"Bad Header": "1"})
    # Host 允许：内网 vhost 网关的合法配置，禁了会打断存量服务
    assert mcp_auth_policy._parse_auth_headers({"Host": "gateway.internal"}) == {
        "Host": "gateway.internal"
    }


def test_resolve_mcp_auth_headers_deduplicates_case_and_registers_redaction():
    secret_token = "sk-static-token-9876543210"
    api_key = "ak-static-header-abcdef123456"
    server = SimpleNamespace(
        auth_headers=mcp_auth_policy.encrypt_mcp_auth_headers(
            {"authorization": "Bearer old", "X-Api-Key": api_key, "X-Tenant": "t1"}
        ),
        fixed_token_encrypted=get_api_key_manager().encrypt_api_key(secret_token),
    )

    # 脱敏池是请求级 ContextVar，测试里没有请求上下文，靠 reset 前后夹住同一个上下文。
    reset_injected()
    try:
        resolved = mcp_auth_policy.resolve_mcp_auth_headers(server)
        auth_keys = [k for k in resolved if k.lower() == "authorization"]
        assert auth_keys == ["Authorization"]
        assert resolved["Authorization"] == f"Bearer {secret_token}"
        assert resolved["X-Api-Key"] == api_key

        leaked = f"echoed {secret_token} and {api_key} with tenant t1"
        sanitized = redact_value(leaked, where="test")
        assert secret_token not in sanitized
        assert api_key not in sanitized
        assert "t1" in sanitized
    finally:
        reset_injected()


def test_user_assertion_issuer_override_priority():
    pem = mcp_auth_policy.generate_mcp_private_key_pem()
    server = SimpleNamespace(
        user_assertion_enabled=True,
        user_assertion_audience="mcp:srv-1",
        user_assertion_key_id="kid-1",
        user_assertion_header="X-Nanzi-User-Assertion",
        user_assertion_issuer="custom-server-issuer",
        user_assertion_private_key_encrypted=get_api_key_manager().encrypt_api_key(pem),
        auth_headers="{}",
        fixed_token_encrypted=None,
    )
    import jwt

    # 未显式传 issuer 时沿用 server.user_assertion_issuer
    default_headers = mcp_auth_policy.build_mcp_headers(
        server,
        user_info={"user_id": "42", "user_name": "alice"},
        agent_info={"agent_id": "test-agent", "agent_name": "回归测试"},
    )
    default_claims = jwt.decode(
        default_headers["X-Nanzi-User-Assertion"], options={"verify_signature": False}
    )
    assert default_claims["iss"] == "custom-server-issuer"

    # 显式传入 issuer 时调用方覆盖生效
    override_headers = mcp_auth_policy.build_mcp_user_assertion_headers(
        server,
        user_info={"user_id": "42", "user_name": "alice"},
        agent_info={"agent_id": "test-agent", "agent_name": "回归测试"},
        issuer="caller-override-issuer",
    )
    override_claims = jwt.decode(
        override_headers["X-Nanzi-User-Assertion"], options={"verify_signature": False}
    )
    assert override_claims["iss"] == "caller-override-issuer"


# ---------------------------------------------------------------------------
# 3. MCP 客户端超时修复、Streamable HTTP 300s 读超时、重试透传与夜间巡检系统凭据
# ---------------------------------------------------------------------------

def test_streamable_http_client_defaults_to_300s_read_timeout():
    client = _create_no_redirect_mcp_http_client()
    try:
        assert client.timeout.connect == 30.0
        assert client.timeout.read == 300.0
        assert client.follow_redirects is False
    finally:
        asyncio.run(client.aclose())


def test_timeout_exceptions_are_not_treated_as_transport_errors():
    assert _is_transport_error(httpx.ReadTimeout("t")) is False
    assert _is_transport_error(httpx2.ReadTimeout("t")) is False
    assert _is_transport_error(McpToolCallTimeout("t")) is False


async def test_direct_http_rpc_never_passes_none_timeout_and_preserves_timeout_on_retry(monkeypatch):
    seen_timeouts: list = []

    class _FakeResp:
        def __init__(self, status_code: int, body: dict):
            self.status_code = status_code
            self._body = body
            self.headers = {"content-type": "application/json"}
            self.text = json.dumps(body)

        def json(self):
            return self._body

    responses = [
        _FakeResp(400, {"error": {"code": -32000, "message": "Session not found"}}),
        _FakeResp(200, {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}),
    ]

    class _FakeHttpClient:
        async def post(self, url, *, json=None, headers=None, timeout=None):
            seen_timeouts.append(timeout)
            return responses.pop(0)

    session_mgr = McpSseSession("srv-direct", "https://gw.example.com/mcp", {})
    session_mgr.mcp_session_id = "sid-expired"
    monkeypatch.setattr(session_mgr, "get_http_client", lambda: _FakeHttpClient())
    monkeypatch.setattr(McpClientService, "_recover_direct_http_session", AsyncMock())

    # 1) 显式超时在会话过期重试时必须透传
    res = await McpClientService._direct_http_rpc(
        session_mgr, "tools/call", {"name": "ping"}, timeout=42.5
    )
    assert res == {"ok": True}
    assert seen_timeouts == [42.5, 42.5]

    # 2) 未传 timeout 时必须传 httpx.USE_CLIENT_DEFAULT，绝不传 None
    seen_timeouts.clear()
    responses.append(_FakeResp(200, {"jsonrpc": "2.0", "id": 4, "result": {"tools": []}}))
    await McpClientService._direct_http_rpc(session_mgr, "tools/list", {})
    assert seen_timeouts == [httpx.USE_CLIENT_DEFAULT]


async def test_direct_http_rpc_raises_mcp_tool_call_timeout_on_httpx_timeout(monkeypatch):
    class _TimeoutClient:
        async def post(self, url, *, json=None, headers=None, timeout=None):
            raise httpx.ReadTimeout("read timed out")

    session_mgr = McpSseSession("srv-timeout", "https://gw.example.com/mcp", {})
    monkeypatch.setattr(session_mgr, "get_http_client", lambda: _TimeoutClient())

    with pytest.raises(McpToolCallTimeout):
        await McpClientService._direct_http_rpc(session_mgr, "tools/call", {"name": "slow"}, timeout=1.0)


async def test_list_remote_tools_injects_system_global_bindings_without_user_info(monkeypatch):
    applied_calls: list[dict] = []
    captured_session_kwargs: list[dict] = []

    fake_server = SimpleNamespace(
        id="srv-global-1",
        server_name="platform-svc",
        scope="global",
        user_assertion_enabled=False,
        auth_headers="{}",
        fixed_token_encrypted=None,
        sse_url="https://api.example.com/mcp",
    )

    async def _fake_load_server(cls, server_id):
        return fake_server

    async def _fake_apply_bindings(server, headers, *, user_info=None, denials=None, used=None, allow_system_global=False):
        applied_calls.append({"user_info": user_info, "allow_system_global": allow_system_global})
        if used is not None:
            used["user_id"] = "__system__"
            used["slots"] = ["header:Authorization"]
        return {**headers, "Authorization": "Bearer sys-secret"}

    class _FakeMgr:
        is_direct_http = True
        closed = False

        async def close(self):
            self.closed = True

    fake_mgr = _FakeMgr()

    async def _fake_get_session(cls, server_id, **kwargs):
        captured_session_kwargs.append(kwargs)
        return fake_mgr

    monkeypatch.setattr(McpClientService, "_load_server", classmethod(_fake_load_server))
    monkeypatch.setattr(client_mod, "apply_mcp_bindings", _fake_apply_bindings)
    monkeypatch.setattr(McpClientService, "get_session", classmethod(_fake_get_session))
    monkeypatch.setattr(McpClientService, "_list_direct_http_tools", classmethod(AsyncMock(return_value=[])))

    await McpClientService.list_remote_tools("srv-global-1", user_info=None)

    assert applied_calls == [{"user_info": None, "allow_system_global": True}]
    assert captured_session_kwargs[0]["auth_headers"]["Authorization"] == "Bearer sys-secret"
    assert str(captured_session_kwargs[0]["session_key"]).startswith("srv-global-1:probe:sys:")
    assert fake_mgr.closed is True


# ---------------------------------------------------------------------------
# 4. mcp.py 核心修复：个人 MCP 不脱敏 / 接受分享 create 权限 / 改名类型保真 / verify finally
# ---------------------------------------------------------------------------

def test_personal_scope_is_not_masked_for_non_admin_source_contract():
    """核心 P0：非管理员只有访问 global 平台服务才脱敏，个人私有服务保留完整字段。"""
    source = open("app/api/portal/endpoints/mcp.py", encoding="utf-8").read()
    assert "if not is_admin and s.scope == \"global\":\n            _mask_platform_server_for_read_only(item)" in source


def test_accept_share_route_requires_create_permission():
    route = next(
        r for r in mcp_endpoints.router.routes
        if getattr(r, "path", "") == "/shares/received/{share_id}/accept"
    )
    assert len(route.dependencies) >= 1
    source = open("app/api/portal/endpoints/mcp.py", encoding="utf-8").read()
    accept_head = source.split("async def accept_mcp_received_share(", 1)[0].rsplit("@router.post", 1)[1]
    assert "element:mcp_management:create" in accept_head


async def test_verify_cleans_temp_session_even_on_cancelled_error(monkeypatch):
    closed = {"flag": False}

    async def _cancel_list(cls, server_id, **kwargs):
        raise asyncio.CancelledError()

    orig_close = McpSseSession.close

    async def _track_close(self):
        closed["flag"] = True
        await orig_close(self)

    monkeypatch.setattr(McpClientService, "list_remote_tools", classmethod(_cancel_list))
    monkeypatch.setattr(McpSseSession, "close", _track_close)

    req = mcp_endpoints.McpVerifyRequest(sse_url="https://example.com/mcp")
    before_keys = set(McpClientService._sessions.keys())

    with pytest.raises(asyncio.CancelledError):
        await mcp_endpoints.verify_mcp_server(
            data=req,
            db=AsyncMock(),
            user={"user_id": 1, "role": "admin"},
        )

    assert closed["flag"] is True
    assert set(McpClientService._sessions.keys()) == before_keys


async def test_load_verify_saved_server_blocks_admin_using_other_users_personal_server():
    other_personal = SimpleNamespace(id="srv-p1", scope="personal", user_id=99)

    class _FakeResult:
        def scalar_one_or_none(self):
            return other_personal

    db = SimpleNamespace(execute=AsyncMock(return_value=_FakeResult()))
    req = mcp_endpoints.McpVerifyRequest(server_id="srv-p1", sse_url="https://attacker.example.com/mcp")

    with pytest.raises(HTTPException) as exc_info:
        await mcp_endpoints._load_verify_saved_server(
            db, req, {"user_id": 1, "role": "admin"}
        )
    assert exc_info.value.status_code == 403


def test_rename_tool_reference_preserves_scalar_and_json_string_types():
    assert mcp_endpoints._rename_tool_reference("123", "old", "new") == "123"
    assert mcp_endpoints._rename_tool_reference("true", "old", "new") == "true"
    assert mcp_endpoints._rename_tool_reference('["other:tool"]', "old", "new") == '["other:tool"]'
    renamed_json = mcp_endpoints._rename_tool_reference('["old:search", {"name": "old:fetch"}]', "old", "new")
    assert isinstance(renamed_json, str)
    assert json.loads(renamed_json) == ["new:search", {"name": "new:fetch"}]


# ---------------------------------------------------------------------------
# 5. SAVEPOINT 事务隔离（PG 双库兼容）
# ---------------------------------------------------------------------------

async def test_write_security_audit_uses_savepoint_and_truncates_fields():
    nested_entered = {"count": 0}
    added: list = []

    class _Nested:
        async def __aenter__(self):
            nested_entered["count"] += 1
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeDb:
        def begin_nested(self):
            return _Nested()

        def add(self, obj):
            added.append(obj)

        async def flush(self):
            raise RuntimeError("simulated db error")

    await security_audit.write_security_audit(
        _FakeDb(),
        event_type="e" * 100,
        request_id="r" * 100,
        client_id="c" * 100,
        user_id="u" * 100,
        actor_user_id="a" * 100,
        error_code="x" * 200,
    )
    assert nested_entered["count"] == 1
    assert len(added[0].request_id) == 64
    assert len(added[0].client_id) == 64
    assert len(added[0].error_code) == 128


async def test_tool_ref_health_uses_savepoint_instead_of_outer_rollback():
    called = {"nested": 0, "rollback": 0}

    class _Nested:
        async def __aenter__(self):
            called["nested"] += 1
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeDb:
        def begin_nested(self):
            return _Nested()

        async def execute(self, stmt):
            raise RuntimeError("relation sys_script_tools does not exist")

        async def rollback(self):
            called["rollback"] += 1

    await tool_ref_health._active_script_tool_names(_FakeDb(), {"custom_script"})
    assert called["nested"] == 1
    assert called["rollback"] == 0


def test_parse_auth_headers_non_strict_skips_legacy_dirty_headers():
    # 读取历史存量数据时 strict=False 跳过非法/保留头，不炸列表也不外发非法头。
    # ``Host`` 不算非法：内网网关按 vhost 路由时它是合法配置，存量里已经配了的不该被静默丢掉。
    parsed = mcp_auth_policy._parse_auth_headers(
        {
            "X-Valid": "ok",
            "Host": "gateway.internal",
            "Bad Header": "1",
            "X-Crlf": "a\r\nb",
            "Content-Length": "5",
        },
        strict=False,
    )
    assert parsed == {"X-Valid": "ok", "Host": "gateway.internal"}


def test_verify_auth_headers_patch_rejects_reserved_and_crlf_headers():
    from fastapi import HTTPException

    data = mcp_endpoints.McpVerifyRequest(
        server_name="demo",
        sse_url="https://mcp.example.com/mcp",
        auth_headers_patch={"Content-Length": "5"},
    )
    with pytest.raises(HTTPException) as exc_info:
        mcp_endpoints._build_verify_auth_headers(None, data)
    assert exc_info.value.status_code == 400

    # Host 是可配置的（内网 vhost 网关），但控制字符仍然拒绝
    host_ok = mcp_endpoints.McpVerifyRequest(
        server_name="demo",
        sse_url="https://mcp.example.com/mcp",
        auth_headers_patch={"Host": "gateway.internal"},
    )
    assert mcp_endpoints._build_verify_auth_headers(None, host_ok) == {"Host": "gateway.internal"}

    crlf_data = mcp_endpoints.McpVerifyRequest(
        server_name="demo",
        sse_url="https://mcp.example.com/mcp",
        auth_headers_patch={"X-Custom": "ok\r\nX-Injected: 1"},
    )
    with pytest.raises(HTTPException) as exc_info2:
        mcp_endpoints._build_verify_auth_headers(None, crlf_data)
    assert exc_info2.value.status_code == 400


# ---------------------------------------------------------------------------
# 6. definition_diff 嵌套属性 / $ref / $defs 与畸形 enum 防御
# ---------------------------------------------------------------------------

def test_definition_diff_detects_nested_properties_and_ref_enum_changes():
    before = {
        "type": "object",
        "$defs": {
            "Filter": {
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["open", "closed"]}},
                "required": ["status"],
            }
        },
        "properties": {"filter": {"$ref": "#/$defs/Filter"}},
    }
    after = {
        "type": "object",
        "$defs": {
            "Filter": {
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["open"]}},
                "required": ["status", "tenant_id"],
            }
        },
        "properties": {"filter": {"$ref": "#/$defs/Filter"}},
    }
    changes = definition_diff.diff_parameter_schemas(before, after)
    assert len(changes) == 1
    assert changes[0]["param"] == "filter"
    assert changes[0]["severity"] == "breaking"


def test_definition_diff_handles_malformed_non_list_enum_without_crashing():
    before = {"type": "object", "properties": {"mode": {"type": "string", "enum": 123}}}
    after = {"type": "object", "properties": {"mode": {"type": "string", "enum": ["fast"]}}}
    changes = definition_diff.diff_parameter_schemas(before, after)
    assert len(changes) == 1
    assert changes[0]["kind"] == "param_enum"


# ---------------------------------------------------------------------------
# 7. mcp_share_service 过滤僵尸工具、换源不带旧工具、筛选 total 正确
# ---------------------------------------------------------------------------

async def test_accept_share_skips_stale_tools_and_drops_tools_on_url_override(monkeypatch):
    from contextlib import contextmanager
    from unittest.mock import patch

    @contextmanager
    def _mock_notify():
        with patch.object(share_svc, "_notify", AsyncMock()):
            yield

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalar_one_or_none(self):
            return self._rows[0] if self._rows else None

        def first(self):
            # accept_share 会先查一次重名（_server_name_taken 用 .first()），
            # 这里必须照真 Result 的语义返回「首行或 None」，缺了会直接 AttributeError。
            return self._rows[0] if self._rows else None

        def scalars(self):
            return self

        def all(self):
            return list(self._rows)

    class _FakeSession:
        def __init__(self, *results):
            self._results = list(results)
            self.added = []

        async def execute(self, stmt):
            return self._results.pop(0) if self._results else _FakeResult([])

        def add(self, obj):
            self.added.append(obj)

        async def flush(self):
            return None

    payload = {
        "version": 1,
        "config": {
            "server_name": "priv-7-demo",
            "sse_url": "https://origin.example.com/mcp",
            "enabled_status": 1,
        },
        "tools": [
            {"tool_name": "priv-7-demo:alive", "tool_description": "ok", "parameter_schema": "{}", "is_published": True, "is_available": True},
            {"tool_name": "priv-7-demo:dead", "tool_description": "gone", "parameter_schema": "{}", "is_published": False, "is_available": False},
        ],
    }

    # 1) 不改地址：只安装存活工具，跳过僵尸工具
    row1 = SimpleNamespace(
        id="s1", source_user_id=7, source_server_id="srv-7",
        server_name="priv-7-demo", remark=None, sse_url="https://origin.example.com/mcp",
        payload=json.dumps(payload), include_credentials=True,
        target_user_id=9, status="PENDING", message=None,
        accepted_server_id=None, responded_at=None,
    )
    sess1 = _FakeSession(_FakeResult([row1]), _FakeResult([]))
    with _mock_notify():
        res1 = await share_svc.accept_share(sess1, user={"user_id": 9, "user_name": "bob"}, share_id="s1")
    cached1 = [obj for obj in sess1.added if isinstance(obj, McpToolCache)]
    assert len(cached1) == 1
    assert cached1[0].tool_name.endswith(":alive")
    assert res1["server"]["tool_count"] == 1

    # 2) 改地址换源：不复制源服务的旧工具且 enabled_status=0
    #    row1 已被第一次接受改成 ACCEPTED 且 payload 被清空，row2 不能直接继承它的状态位。
    row2 = SimpleNamespace(
        **{
            **row1.__dict__,
            "id": "s2",
            "status": "PENDING",
            "payload": json.dumps(payload),
            "accepted_server_id": None,
            "responded_at": None,
        }
    )
    sess2 = _FakeSession(_FakeResult([row2]), _FakeResult([]))
    with _mock_notify():
        res2 = await share_svc.accept_share(
            sess2,
            user={"user_id": 9, "user_name": "bob"},
            share_id="s2",
            sse_url="https://new-host.example.com/mcp",
        )
    srv2 = next(obj for obj in sess2.added if isinstance(obj, McpServer))
    cached2 = [obj for obj in sess2.added if isinstance(obj, McpToolCache)]
    assert srv2.enabled_status == 0
    assert cached2 == []
    assert res2["server"]["tool_count"] == 0


# ---------------------------------------------------------------------------
# 7. 夜间巡检的系统级全局凭据注入（apply_mcp_bindings(allow_system_global=True)）
# ---------------------------------------------------------------------------

async def _run_system_injection(
    monkeypatch, rows, *, scope="global", headers=None, denial=None, grant_ids=None
):
    """用桩替身跑一遍系统上下文注入，返回 (resolved_headers, denials, used)。

    ``grant_ids`` 是「这些系统凭据有有效授权」的集合；默认全部授权（只测注入逻辑），
    传空集合用来验证「零 grant 的系统凭据不许被后台解密外发」。
    """
    import app.core.orm as orm_mod
    from app.services.secrets import injection as inj
    from app.services.secrets import policy as policy_mod
    from app.services.secrets import resolver as resolver_mod

    class _Rows:
        def __init__(self, items):
            self._items = items

        def all(self):
            return list(self._items)

        def scalars(self):
            return self

    class _Session:
        def __init__(self, items, grants):
            self._items = items
            self._grants = grants

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            # 注入函数会在同一个会话里多查一次「哪些凭据有有效授权」
            if "secret_grant" in str(stmt).lower():
                return _Rows(list(self._grants))
            return _Rows(self._items)

    async def _platform_flags():
        return (None, True, None)

    async def _max_inject():
        return 10

    if grant_ids is None:
        grant_ids = [getattr(row, "id", "") for _binding, row in rows]
    monkeypatch.setattr(
        orm_mod, "AsyncSessionLocal", lambda: _Session(rows, grant_ids)
    )
    monkeypatch.setattr(resolver_mod, "_to_record", lambda row: row)
    monkeypatch.setattr(resolver_mod.SecretResolver, "_platform_flags", _platform_flags)
    monkeypatch.setattr(
        resolver_mod.SecretResolver, "_pick_ciphertext", staticmethod(lambda row, now: "ct")
    )
    monkeypatch.setattr(resolver_mod.SecretResolver, "_decrypt", staticmethod(lambda ct: "s3cr3t"))
    monkeypatch.setattr(policy_mod, "evaluate_secret", lambda record, ctx, now=None: denial)
    monkeypatch.setattr(inj, "_max_inject_per_call", _max_inject)

    server = SimpleNamespace(id="srv-sys", scope=scope, sse_url="https://mcp.example.com/mcp")
    denials: dict[str, str] = {}
    used: dict = {}
    resolved = await inj.apply_mcp_bindings(
        server,
        headers if headers is not None else {"Authorization": "Bearer ${CREDENTIAL:k1}"},
        user_info=None,
        denials=denials,
        used=used,
        allow_system_global=True,
    )
    return resolved, denials, used


def _binding(slot, *, bound_by=None, strategy="fixed"):
    return SimpleNamespace(slot=slot, bound_by_user_id=bound_by, strategy=strategy)


def _secret(*, is_system=True, key="k1", secret_id=None):
    return SimpleNamespace(
        id=secret_id or f"sec-{key}-{'sys' if is_system else 'user'}",
        secret_key=key,
        is_system=is_system,
        value_format="text",
    )


async def test_system_injection_only_uses_global_system_fixed_bindings(monkeypatch):
    """巡检只能拿到「平台服务 + 全局绑定 + fixed 策略 + 系统凭据」，个人层一律不碰。"""
    rows = [
        (_binding("header:Authorization"), _secret()),                      # 命中
        (_binding("header:X-Personal", bound_by=7), _secret()),             # 个人绑定
        (_binding("header:X-Rule", strategy="rule"), _secret()),            # 规则行
        (_binding("header:X-Mine"), _secret(is_system=False)),              # 个人凭据
        (_binding("param:api_key"), _secret()),                             # 非 header 槽位
    ]
    resolved, denials, used = await _run_system_injection(
        monkeypatch,
        rows,
        headers={
            "Authorization": "Bearer ${CREDENTIAL:k1}",
            "X-Personal": "keep",
            "X-Rule": "keep",
            "X-Mine": "keep",
        },
    )
    assert resolved["Authorization"] == "Bearer s3cr3t"
    assert resolved["X-Personal"] == "keep"
    assert resolved["X-Rule"] == "keep"
    assert resolved["X-Mine"] == "keep"
    assert denials == {}
    # 会话复用键靠它区分「这条链路用的是系统语境」
    assert used["user_id"] == "__system__"
    assert used["slots"] == ["header:Authorization"]


async def test_system_injection_skips_non_global_server(monkeypatch):
    """个人服务绝不走系统注入，占位符原样留下交给调用方报「未解析」。"""
    rows = [(_binding("header:Authorization"), _secret())]
    resolved, denials, used = await _run_system_injection(
        monkeypatch, rows, scope="personal"
    )
    assert resolved["Authorization"] == "Bearer ${CREDENTIAL:k1}"
    assert used == {}
    assert denials, "未解析的占位符必须被标记，否则调用方会以为注入成功"


async def test_system_injection_records_policy_denial(monkeypatch):
    """凭据被停用/过期/域名不符时记拒因，让巡检能说清是配错了还是策略拒了。"""
    rows = [(_binding("header:Authorization"), _secret())]
    resolved, denials, used = await _run_system_injection(monkeypatch, rows, denial="expired")
    assert resolved["Authorization"] == "Bearer ${CREDENTIAL:k1}"
    assert denials.get("header:Authorization") == "expired"


async def test_system_injection_warns_but_still_injects_secret_without_grant(
    monkeypatch, caplog
):
    """零 grant 的系统凭据：只告警，不拒绝注入（存量口径不变）。

    用户态解析系统凭据前会判「是否 grant 给这个用户」，巡检没有操作者可比对。
    这里刻意不阻断：阻断会让存量平台服务的探测仅因缺一条 grant 就变红，而真实
    调用本来就已是 denied_by_grant；但必须留痕，否则「谁都没被授权的凭据被后台
    解密外发」完全没有痕迹。
    """
    rows = [(_binding("header:Authorization"), _secret())]
    with caplog.at_level("WARNING", logger="app.services.secrets.injection"):
        resolved, denials, used = await _run_system_injection(
            monkeypatch, rows, grant_ids=[]
        )
    # 注入照旧（存量行为不变）
    assert resolved["Authorization"] == "Bearer s3cr3t"
    assert denials == {}, "只告警，不记拒因——记了探测就会整条失败"
    assert used["slots"] == ["header:Authorization"]
    assert any("没有任何有效授权记录" in record.message for record in caplog.records)
