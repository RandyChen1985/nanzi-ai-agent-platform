from pathlib import Path
import time

import httpx
import pytest
from mcp.server.auth.provider import AccessToken

from app.services.config_service import ConfigService
from app.services.mcp import platform_mcp as platform_mcp_module
from app.services.mcp.platform_mcp import (
    PLATFORM_MCP_METHODS,
    PLATFORM_MCP_RESOURCE_URI_SUFFIX,
    get_method_definition,
    platform_mcp,
    platform_mcp_lifespan,
)


pytestmark = pytest.mark.no_infrastructure


def test_platform_mcp_is_one_server_with_knowledge_search_extension_point():
    method = get_method_definition("knowledge_search")

    assert method is not None
    assert method.scope == "knowledge:search"
    assert method.capability_group == "knowledge"
    assert method.requires_user is True
    assert "knowledge_search" in {item.name for item in PLATFORM_MCP_METHODS}


def test_platform_mcp_resource_path_is_stable():
    assert PLATFORM_MCP_RESOURCE_URI_SUFFIX == "/mcp/platform"


def test_platform_mcp_source_declares_bearer_resource_server_and_tool():
    source = Path("app/services/mcp/platform_mcp.py").read_text(encoding="utf-8")

    assert "PlatformMcpTokenVerifier" in source
    assert "AuthSettings" in source
    assert 'name="knowledge_search"' in source
    assert "get_access_token" in source


def test_platform_mcp_declares_client_and_user_rate_limits():
    source = Path("app/services/mcp/platform_mcp.py").read_text(encoding="utf-8")

    assert "check_platform_mcp_rate_limit" in source
    assert "client_id" in source
    assert "user_id" in source
    assert "status_code=429" in source


@pytest.mark.asyncio
async def test_platform_mcp_http_lifecycle_authenticates_and_calls_knowledge_tool(monkeypatch):
    async def fake_verify_token(_token: str) -> AccessToken:
        return AccessToken(
            token="test-token",
            client_id="test-client",
            scopes=["knowledge:search"],
            expires_at=int(time.time()) + 300,
            resource="http://localhost:8001/mcp/platform",
            subject="123",
            claims={"jti": "test-jti", "user_name": "tester"},
        )

    async def fake_config_get(_key: str, default=None):
        return default

    async def fake_scope(_principal, _requested):
        return ["kb-a"], ["user_id=123", "client_id=test-client"]

    class FakeRagFlowClient:
        async def retrieve(self, *_args, **_kwargs):
            return [{"content": "已收到", "dataset_id": "kb-a"}]

    monkeypatch.setattr(platform_mcp._token_verifier, "verify_token", fake_verify_token)
    monkeypatch.setattr(platform_mcp_module, "platform_mcp_resource_url", lambda: "http://localhost:8001/mcp/platform")
    monkeypatch.setattr(platform_mcp_module, "is_platform_mcp_enabled", lambda: _true())
    monkeypatch.setattr(platform_mcp_module, "is_platform_mcp_capability_enabled", lambda _group: _true())
    monkeypatch.setattr(platform_mcp_module, "is_knowledge_base_enabled", lambda: _true())
    monkeypatch.setattr(ConfigService, "get", fake_config_get)
    monkeypatch.setattr(platform_mcp_module, "_resolve_knowledge_scope", fake_scope)
    monkeypatch.setattr(platform_mcp_module, "RagFlowClient", lambda **_kwargs: FakeRagFlowClient())
    monkeypatch.setattr(platform_mcp_module, "_write_audit", _noop_audit)

    mcp_app = platform_mcp.streamable_http_app()
    async with platform_mcp_lifespan():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_app),
            base_url="http://localhost:8001",
            headers={"Authorization": "Bearer test-token", "Accept": "application/json, text/event-stream"},
        ) as client:
            initialize = await client.post(
                "/platform",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "contract-test", "version": "1.0"},
                    },
                },
            )
            assert initialize.status_code == 200, initialize.text

            tools = await client.post(
                "/platform",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            assert tools.status_code == 200, tools.text
            assert "knowledge_search" in tools.text

            called = await client.post(
                "/platform",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "knowledge_search", "arguments": {"query": "测试"}},
                },
            )
            assert called.status_code == 200, called.text
            assert "已收到" in called.text


async def _true():
    return True


async def _noop_audit(*_args, **_kwargs):
    return None
