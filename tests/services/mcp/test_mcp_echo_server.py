import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.mcp import echo_server
from app.services.mcp.echo_server import (
    build_echo_diagnostics,
    echo_mcp,
    echo_mcp_http_app,
    echo_mcp_lifespan,
)
from app.services.mcp.user_context_assertion import issue_user_assertion


pytestmark = pytest.mark.no_infrastructure


def test_echo_mcp_http_app_is_cached_singleton():
    """同 platform：工厂缓存实例，避免运行期二次调用顶掉已 run 的 manager。"""
    first_app = echo_mcp_http_app()
    first_manager = echo_mcp.session_manager

    second_app = echo_mcp_http_app()

    assert second_app is first_app
    assert echo_mcp.session_manager is first_manager


def test_echo_transport_security_uses_public_url_host_and_origin():
    security = echo_server.build_echo_transport_security("http://103.79.25.80:8001")

    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["103.79.25.80:8001"]
    assert security.allowed_origins == ["http://103.79.25.80:8001"]


def test_echo_transport_security_supports_https_domain_without_port():
    security = echo_server.build_echo_transport_security("https://mcp.example.com/")

    assert security is not None
    assert security.allowed_hosts == ["mcp.example.com"]
    assert security.allowed_origins == ["https://mcp.example.com"]


@pytest.mark.parametrize("public_url", ["", "not-a-url", "ftp://mcp.example.com", "https:///missing-host"])
def test_echo_transport_security_ignores_invalid_public_url(public_url):
    assert echo_server.build_echo_transport_security(public_url) is None


def test_echo_base_url_prefers_valid_public_url_and_falls_back_for_invalid_url():
    assert echo_server.resolve_echo_base_url(
        "http://proxy.internal:8001/",
        "https://mcp.example.com/",
    ) == "https://mcp.example.com"
    assert echo_server.resolve_echo_base_url(
        "http://proxy.internal:8001/",
        "not-a-url",
    ) == "http://proxy.internal:8001"


def _server(**overrides):
    values = {
        "auth_headers": json.dumps({"Authorization": "Bearer echo-token"}),
        "fixed_token_encrypted": None,
        "credential_mode": "fixed_token_signed_user",
        "user_assertion_enabled": True,
        "user_assertion_header": "X-Nanzi-User-Assertion",
        "user_assertion_audience": "mcp:echo",
        "user_assertion_key_id": "echo-key-1",
        "user_assertion_issuer": "nanzi-platform",
    }
    values.update(overrides)
    return type("McpServerStub", (), values)()


def _assertion(private_key, *, request_id="req-echo-1"):
    return issue_user_assertion(
        user_info={
            "user_id": "123",
            "user_name": "zhangsan",
            "real_name": "张三",
            "dept_code": "D001",
            "extra_data": {"region": "east", "employee_level": "L3"},
        },
        agent_info={
            "agent_id": "agent-001",
            "agent_version_id": "version-001",
            "agent_name": "测试助手",
        },
        audience="mcp:echo",
        request_id=request_id,
        private_key=private_key,
        key_id="echo-key-1",
        issuer="nanzi-platform",
    )


def test_echo_diagnostics_returns_verified_identity_without_raw_credentials():
    private_key = Ed25519PrivateKey.generate()
    assertion = _assertion(private_key)
    authorization = "Bearer echo-token"

    result = build_echo_diagnostics(
        headers={
            "Authorization": authorization,
            "X-Nanzi-User-Assertion": assertion,
            "X-Request-ID": "req-echo-1",
        },
        server=_server(),
        private_key=private_key,
    )

    diagnostics = result["diagnostics"]
    assert result["message"] == "已收到"
    assert diagnostics["authorization_valid"] is True
    assert diagnostics["authorization_masked"] == "Bearer echo***oken"
    assert diagnostics["user_assertion_received"] is True
    assert diagnostics["user_assertion_valid"] is True
    assert diagnostics["user_assertion_masked"].startswith("eyJhbG***")
    assert diagnostics["user_assertion_masked"].endswith("***") is False
    assert diagnostics["request_id_received"] is True
    assert diagnostics["processing_log"] == [
        "已收到 Authorization 请求头",
        "Authorization Bearer Token 校验通过",
        "已收到 X-Nanzi-User-Assertion 请求头",
        "UserContext 签名校验通过",
        "已解析用户、扩展字段、智能体和请求信息",
    ]
    assert diagnostics["verified_user_id"] == "123"
    assert diagnostics["verified_user_context"] == {
        "user_id": "123",
        "user_name": "zhangsan",
        "real_name": "张三",
        "dept_code": "D001",
    }
    assert diagnostics["verified_agent_context"] == {
        "agent_id": "agent-001",
        "agent_version_id": "version-001",
        "agent_name": "测试助手",
    }
    assert diagnostics["custom_attributes"] == {
        "region": "east",
        "employee_level": "L3",
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert authorization not in serialized
    assert assertion not in serialized
    assert "eyJhbGciOi" not in serialized


def test_echo_diagnostics_reports_missing_optional_user_assertion():
    result = build_echo_diagnostics(
        headers={"Authorization": "Bearer echo-token"},
        server=_server(),
        private_key=Ed25519PrivateKey.generate(),
    )

    assert result["diagnostics"] == {
        "authorization_valid": True,
        "authorization_masked": "Bearer echo***oken",
        "user_assertion_received": False,
        "user_assertion_valid": False,
        "user_assertion_masked": None,
        "request_id_received": False,
        "processing_log": [
            "已收到 Authorization 请求头",
            "Authorization Bearer Token 校验通过",
            "未收到 X-Nanzi-User-Assertion 请求头",
        ],
    }


def test_echo_diagnostics_rejects_invalid_authorization():
    with pytest.raises(PermissionError, match="Authorization"):
        build_echo_diagnostics(
            headers={"Authorization": "Bearer wrong-token"},
            server=_server(),
            private_key=Ed25519PrivateKey.generate(),
        )


def test_echo_diagnostics_rejects_invalid_user_assertion():
    private_key = Ed25519PrivateKey.generate()
    assertion = _assertion(private_key).rsplit(".", 1)[0] + ".invalid-signature"

    with pytest.raises(PermissionError, match="用户身份断言"):
        build_echo_diagnostics(
            headers={
                "Authorization": "Bearer echo-token",
                "X-Nanzi-User-Assertion": assertion,
            },
            server=_server(),
            private_key=private_key,
        )


@pytest.mark.asyncio
async def test_streamable_http_app_has_initialized_task_group_inside_host_lifespan():
    from starlette.applications import Starlette

    app = Starlette()
    app.mount(
        "/mcp/echo",
        echo_server.EchoServerIdMiddleware(echo_mcp.streamable_http_app()),
    )

    async with echo_mcp_lifespan():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://echo.test",
        ) as client:
            global_response = await client.get("/mcp/echo/mcp")
            personal_response = await client.get("/mcp/echo/personal-1/mcp")

    assert global_response.status_code != 500
    assert "Task group is not initialized" not in global_response.text
    # 个人 Echo 的 /{server_id}/mcp 必须命中同一个 FastMCP 端点，而不是 404。
    assert personal_response.status_code != 404
    assert personal_response.status_code == global_response.status_code


def test_echo_server_id_header_selects_instance():
    assert echo_server.echo_server_id_from_headers({}) is None
    assert echo_server.echo_server_id_from_headers(
        {"X-Nanzi-Echo-Server": "personal-id"}
    ) == "personal-id"


@pytest.mark.asyncio
async def test_echo_middleware_rewrites_personal_path_and_strips_forged_header():
    captured = {}

    async def app(scope, receive, send):
        captured.update(scope)

    middleware = echo_server.EchoServerIdMiddleware(app)
    await middleware(
        {
            "type": "http",
            "path": "/mcp/echo/personal-1/mcp",
            "root_path": "/mcp/echo",
            "headers": [
                (b"x-nanzi-echo-server", b"forged"),
                (b"authorization", b"Bearer t"),
            ],
        },
        None,
        None,
    )

    assert captured["path"] == "/mcp/echo/mcp"
    assert captured["raw_path"] == b"/mcp/echo/mcp"
    assert (b"x-nanzi-echo-server", b"personal-1") in captured["headers"]
    assert (b"x-nanzi-echo-server", b"forged") not in captured["headers"]
    assert (b"authorization", b"Bearer t") in captured["headers"]


@pytest.mark.asyncio
async def test_echo_middleware_keeps_global_path_without_injected_id():
    captured = {}

    async def app(scope, receive, send):
        captured.update(scope)

    middleware = echo_server.EchoServerIdMiddleware(app)
    await middleware(
        {
            "type": "http",
            "path": "/mcp/echo/mcp",
            "root_path": "/mcp/echo",
            "headers": [],
        },
        None,
        None,
    )

    assert captured["path"] == "/mcp/echo/mcp"
    assert captured["headers"] == []
