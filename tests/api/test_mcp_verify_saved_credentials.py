"""编辑态连接测试必须复用已保存凭据，地址变更时也沿用，避免用户重复输入 Token。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import mcp
from app.services.mcp import mcp_auth_policy
from app.services.mcp.user_context_assertion import verify_user_assertion
from app.utils.encryption import get_api_key_manager


pytestmark = pytest.mark.no_infrastructure

SAVED_URL = "http://103.79.25.80:8001/mcp/platform"
SAVED_TOKEN = "old-secret-token"


def _saved_server(**overrides):
    server = SimpleNamespace(
        id="srv-1",
        server_name="平台 MCP",
        sse_url=SAVED_URL,
        scope="global",
        user_id=None,
        auth_headers=mcp_auth_policy.encrypt_mcp_auth_headers({"X-Tenant": "tenant-a"}),
        fixed_token_encrypted=get_api_key_manager().encrypt_api_key(SAVED_TOKEN),
        user_assertion_enabled=False,
        user_assertion_header="X-Nanzi-User-Assertion",
        user_assertion_audience=None,
        user_assertion_key_id=None,
        user_assertion_issuer="nanzi-platform",
        user_assertion_private_key_encrypted=None,
    )
    for key, value in overrides.items():
        setattr(server, key, value)
    return server


def _db_returning(server):
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: server))
    return db


def _verify_request(**overrides):
    payload = {
        "server_id": "srv-1",
        "server_name": "平台 MCP",
        "sse_url": SAVED_URL,
        "authorization_enabled": True,
    }
    payload.update(overrides)
    return mcp.McpVerifyRequest(**payload)


async def _run_verify(data, server=None, user=None):
    """执行 /verify 并返回本次探测实际发往 MCP 的 Header。"""
    captured = {}

    async def _capture(server_id):
        session = mcp.McpClientService._sessions.get(server_id)
        captured["headers"] = dict(session.auth_headers) if session else None
        return []

    lister = AsyncMock(side_effect=_capture)
    with patch.object(mcp.McpClientService, "list_remote_tools", lister):
        try:
            await mcp.verify_mcp_server(
                data,
                _db_returning(server) if server is not None else MagicMock(),
                user or {"role": "admin", "user_id": 1},
            )
        finally:
            for key in [key for key in mcp.McpClientService._sessions if key.startswith("verify_")]:
                mcp.McpClientService._sessions.pop(key, None)
    return captured.get("headers")


@pytest.mark.asyncio
async def test_edit_verify_reuses_saved_bearer_token_when_url_unchanged():
    """未修改 Token、未改地址时，探测必须带上已保存的 Bearer Token。"""
    headers = await _run_verify(_verify_request(), server=_saved_server())

    assert headers is not None
    assert headers["Authorization"] == f"Bearer {SAVED_TOKEN}"


@pytest.mark.asyncio
async def test_edit_verify_reuses_saved_extra_headers_when_url_unchanged():
    headers = await _run_verify(_verify_request(), server=_saved_server())

    assert headers["X-Tenant"] == "tenant-a"


@pytest.mark.asyncio
async def test_edit_verify_reuses_saved_token_when_url_changed():
    """地址被改掉时仍沿用已保存凭据，避免用户每次改地址都要重填 Token。"""
    headers = await _run_verify(
        _verify_request(sse_url="https://new.example.com/mcp"),
        server=_saved_server(),
    )

    assert headers is not None
    assert headers["Authorization"] == f"Bearer {SAVED_TOKEN}"
    assert headers["X-Tenant"] == "tenant-a"


@pytest.mark.asyncio
async def test_edit_verify_prefers_newly_entered_token():
    headers = await _run_verify(
        _verify_request(fixed_token="rotated-token"),
        server=_saved_server(),
    )

    assert headers["Authorization"] == "Bearer rotated-token"


@pytest.mark.asyncio
async def test_edit_verify_respects_authorization_disabled():
    headers = await _run_verify(
        _verify_request(authorization_enabled=False),
        server=_saved_server(),
    )

    assert "Authorization" not in headers


@pytest.mark.asyncio
async def test_edit_verify_applies_other_header_patch_on_top_of_saved_headers():
    headers = await _run_verify(
        _verify_request(auth_headers_patch={"X-Tenant": "tenant-b", "X-Extra": "1"}),
        server=_saved_server(),
    )

    assert headers["X-Tenant"] == "tenant-b"
    assert headers["X-Extra"] == "1"
    assert headers["Authorization"] == f"Bearer {SAVED_TOKEN}"


@pytest.mark.asyncio
async def test_new_server_verify_stays_stateless_without_saved_credentials():
    headers = await _run_verify(
        mcp.McpVerifyRequest(
            server_name="新增 MCP",
            sse_url="https://new.example.com/mcp",
            auth_headers="{}",
        )
    )

    assert headers == {}


@pytest.mark.asyncio
async def test_edit_verify_rejects_foreign_personal_server():
    server = _saved_server(scope="personal", user_id=99)

    with pytest.raises(HTTPException) as exc_info:
        await _run_verify(_verify_request(), server=server, user={"role": "user", "user_id": 1})

    assert exc_info.value.status_code == 403


def _assertion_server():
    """已保存用户身份传递配置的 MCP：私钥与 key_id 只能来自保存后的配置。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    server = _saved_server(
        user_assertion_enabled=True,
        user_assertion_audience="mcp:srv-1",
        user_assertion_key_id="key-1",
        user_assertion_private_key_encrypted=get_api_key_manager().encrypt_api_key(pem),
    )
    return server, private_key


@pytest.mark.asyncio
async def test_edit_verify_signs_user_assertion_from_saved_private_key():
    server, private_key = _assertion_server()

    headers = await _run_verify(
        _verify_request(user_assertion_enabled=True),
        server=server,
        user={"role": "admin", "user_id": 7, "user_name": "alice"},
    )

    claims = verify_user_assertion(
        headers["X-Nanzi-User-Assertion"],
        public_key=private_key.public_key(),
        issuer="nanzi-platform",
        audience="mcp:srv-1",
    )
    assert claims["sub"] == "nanzi:user:7"
    assert headers["X-Request-ID"]
    assert headers["Authorization"] == f"Bearer {SAVED_TOKEN}"


@pytest.mark.asyncio
async def test_edit_verify_honours_custom_assertion_header_name():
    server, _ = _assertion_server()

    headers = await _run_verify(
        _verify_request(
            user_assertion_enabled=True,
            user_assertion_header="X-Company-User-Assertion",
        ),
        server=server,
    )

    assert "X-Company-User-Assertion" in headers
    assert "X-Nanzi-User-Assertion" not in headers


@pytest.mark.asyncio
async def test_edit_verify_skips_assertion_when_switch_is_off():
    server, _ = _assertion_server()

    headers = await _run_verify(
        _verify_request(user_assertion_enabled=False),
        server=server,
    )

    assert "X-Nanzi-User-Assertion" not in headers
    assert headers["Authorization"] == f"Bearer {SAVED_TOKEN}"


@pytest.mark.asyncio
async def test_new_server_verify_requires_saved_key_for_user_assertion():
    with pytest.raises(HTTPException) as exc_info:
        await _run_verify(
            mcp.McpVerifyRequest(
                server_name="新增 MCP",
                sse_url="https://new.example.com/mcp",
                user_assertion_enabled=True,
            )
        )

    assert exc_info.value.status_code == 400
    assert "保存" in exc_info.value.detail
