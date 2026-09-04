from pathlib import Path
import json

import pytest

from app.api.portal.endpoints.mcp import (
    McpServerResponse,
    McpServerWrite,
    _apply_mcp_auth_update,
    _default_user_assertion_audience,
)
from app.services.mcp import mcp_auth_policy


pytestmark = pytest.mark.no_infrastructure


def _payload(**overrides):
    payload = {
        "server_name": "CRM",
        "sse_url": "https://crm.example.com/mcp",
        "auth_headers": '{"Authorization":"Bearer fixed-token"}',
    }
    payload.update(overrides)
    return payload


def test_signed_user_mode_allows_system_generated_identity_settings():
    request = McpServerWrite(
        **_payload(
            credential_mode="fixed_token_signed_user",
            user_assertion_enabled=True,
        )
    )
    assert request.user_assertion_audience is None
    assert request.user_assertion_key_id is None
    assert request.user_assertion_issuer == "nanzi-platform"


def test_user_assertion_audience_is_derived_from_mcp_id():
    assert _default_user_assertion_audience("server-123") == "mcp:server-123"


def test_signed_user_mode_accepts_write_only_token_and_policy():
    request = McpServerWrite(
        **_payload(
            credential_mode="fixed_token_signed_user",
            fixed_token="fixed-token",
            user_assertion_enabled=True,
            user_assertion_audience="mcp:crm",
            user_assertion_key_id="key-1",
            user_assertion_issuer="nanzi-platform",
        )
    )

    assert request.fixed_token == "fixed-token"
    assert request.credential_mode == "fixed_token_signed_user"
    assert "fixed_token" not in McpServerResponse.model_fields
    assert "fixed_token_encrypted" not in McpServerResponse.model_fields
    assert "user_assertion_private_key" not in McpServerWrite.model_fields


def test_user_assertion_can_be_enabled_without_bearer_auth_mode():
    request = McpServerWrite(
        **_payload(
            credential_mode="static",
            user_assertion_enabled=True,
            auth_headers="{}",
        )
    )

    assert request.credential_mode == "static"
    assert request.user_assertion_enabled is True


def test_update_model_accepts_write_only_fixed_token():
    request = McpServerWrite(**_payload(fixed_token="rotated-token"))

    assert request.fixed_token == "rotated-token"
    assert "fixed_token" not in request.model_dump()


def test_private_key_is_write_only():
    assert "user_assertion_private_key" not in McpServerResponse.model_fields
    assert "user_assertion_private_key" not in McpServerWrite.model_fields


def test_mcp_response_does_not_include_auth_header_values_for_editing():
    assert "auth_headers" not in McpServerResponse.model_fields
    assert "auth_headers_configured" in McpServerResponse.model_fields
    assert "authorization_configured" in McpServerResponse.model_fields
    assert "masked_auth_headers" in McpServerResponse.model_fields


def test_mcp_auth_summary_separates_authorization_from_masked_dynamic_headers():
    server = type(
        "McpServerStub",
        (),
        {
            "auth_headers": '{"Authorization":"Bearer secret-token","X-Tenant":"tenant-a"}',
            "fixed_token_encrypted": None,
        },
    )()

    configured, masked = mcp_auth_policy.mcp_auth_headers_summary(server)

    assert configured is True
    assert masked == {"X-Tenant": "********"}


def test_mcp_auth_update_replaces_token_and_patches_dynamic_headers_without_echoing_values():
    server = type(
        "McpServerStub",
        (),
        {
            "auth_headers": mcp_auth_policy.encrypt_mcp_auth_headers({"X-Tenant": "tenant-a"}),
            "fixed_token_encrypted": None,
        },
    )()
    data = McpServerWrite(
        **_payload(
            auth_headers="{}",
            authorization_enabled=True,
            fixed_token="new-token",
            auth_headers_patch={"X-Tenant": "tenant-b"},
        )
    )

    _apply_mcp_auth_update(server, data)

    assert mcp_auth_policy.resolve_mcp_auth_headers(server) == {
        "X-Tenant": "tenant-b",
        "Authorization": "Bearer new-token",
    }
    assert "new-token" not in server.auth_headers


def test_mcp_auth_update_can_disable_authorization_and_remove_dynamic_header():
    server = type(
        "McpServerStub",
        (),
        {
            "auth_headers": mcp_auth_policy.encrypt_mcp_auth_headers({"X-Tenant": "tenant-a"}),
            "fixed_token_encrypted": mcp_auth_policy.get_api_key_manager().encrypt_api_key("old-token"),
        },
    )()
    data = McpServerWrite(
        **_payload(
            auth_headers="{}",
            authorization_enabled=False,
            auth_headers_patch={"X-Tenant": None},
        )
    )

    _apply_mcp_auth_update(server, data)

    assert mcp_auth_policy.resolve_mcp_auth_headers(server) == {}
    assert server.fixed_token_encrypted is None


def test_mcp_server_endpoints_store_auth_headers_encrypted_and_return_status_only():
    source = Path("app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")

    assert 'server_data["auth_headers"] = encrypt_mcp_auth_headers(auth_headers)' in source
    assert 'server.auth_headers = encrypt_mcp_auth_headers(data.auth_headers)' in source
    assert "auth_headers_patch" in source
    assert "authorization_enabled" in source
    assert 'response_data["auth_headers"]' not in source
    assert "item.auth_headers_configured = mcp_auth_headers_configured(s)" in source


def test_mcp_auth_headers_support_encrypted_and_legacy_storage():
    headers = {"Authorization": "Bearer secret-token", "X-Tenant": "tenant-a"}

    encrypt_mcp_auth_headers = getattr(mcp_auth_policy, "encrypt_mcp_auth_headers", None)
    resolve_mcp_auth_headers = mcp_auth_policy.resolve_mcp_auth_headers
    assert callable(encrypt_mcp_auth_headers)
    encrypted = encrypt_mcp_auth_headers(headers)
    server = type("McpServerStub", (), {"auth_headers": encrypted})()
    assert encrypted.startswith("enc:v1:")
    assert resolve_mcp_auth_headers(server) == headers

    legacy_server = type("McpServerStub", (), {"auth_headers": json.dumps(headers)})()
    assert resolve_mcp_auth_headers(legacy_server) == headers


def test_legacy_static_mode_remains_valid_without_user_assertion_config():
    request = McpServerWrite(**_payload())
    assert request.credential_mode == "static"
    assert request.user_assertion_enabled is False


def test_mcp_tool_tester_uses_current_user_and_never_returns_assertion_value():
    source = Path("app/api/portal/endpoints/mcp.py").read_text(encoding="utf-8")

    assert 'agent_id": "mcp-tool-tester"' in source
    assert "user_info=test_user_info" in source
    assert 'value_masked": "********"' in source
    assert "X-Nanzi-User-Assertion" in source
