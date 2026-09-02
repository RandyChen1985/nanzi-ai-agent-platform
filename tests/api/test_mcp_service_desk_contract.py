from pathlib import Path

import pytest

from app.api.portal.endpoints import mcp_service


pytestmark = pytest.mark.no_infrastructure


def test_mcp_service_desk_is_separate_from_outbound_mcp_management():
    source = Path("app/api/portal/api.py").read_text(encoding="utf-8")

    assert "mcp_service.router" in source
    assert 'prefix="/mcp-service"' in source
    assert 'prefix="/mcp"' in source


def test_mcp_service_desk_uses_menu_and_element_permissions_without_require_admin():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "menu:mcp_service" in source
    assert "element:mcp_service:client:manage" in source
    assert "element:mcp_service:client:secret_reset" in source
    assert "element:mcp_service:capability:manage" in source
    assert "require_admin" not in source


def test_mcp_service_desk_has_three_layer_switches_and_secret_is_write_only():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "platform_enabled" in source
    assert "knowledge_enabled" in source
    assert '"client_secret": None' in source
    assert "client_secret_hash" not in source.split("def _serialize_client", 1)[1].split("@router", 1)[0]


def test_mcp_service_desk_uses_dedicated_platform_config_storage():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "McpPlatformConfig" in source
    assert "PlatformMcpConfigService" in source
    assert "from app.services.config_service import" not in source
    assert "ConfigService.set_config" not in source


def test_mcp_client_management_is_scoped_to_current_user_id_even_for_admin():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "def _current_user_id" in source
    assert "def _get_owned_client" in source
    assert "McpOAuthClient.created_by == current_user_id" in source
    assert "created_by=current_user_id" in source
    assert 'created_by=str(user.get("user_name") or user.get("user_id"))' not in source

    for endpoint in (
        'async def get_overview(',
        'async def list_audit(',
        'async def list_clients(',
        'async def update_client(',
        'async def delete_client(',
        'async def reset_client_secret(',
        'async def create_current_user_access_token(',
    ):
        segment = source.split(endpoint, 1)[1].split("\n@router", 1)[0]
        assert "current_user_id" in segment or "_get_owned_client" in segment


def test_service_desk_exposes_current_user_access_token_with_expiry_and_permission():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "user-access-token" in source
    assert "McpAccessTokenCreate" in source
    assert "expires_in" in source
    assert "user_id" in source
    assert "element:mcp_service:client:token_issue" in source


def test_client_scope_version_is_returned_and_changes_trigger_reissue_state():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert '"scope_version":' in source
    assert '"needs_token_regeneration":' in source
    assert "scope_changed" in source
    assert "client.scope_version = int(client.scope_version or 1) + 1" in source
    assert "McpOAuthAccessToken.scope_version" in source


def test_current_user_access_token_request_validates_expiry_and_scopes():
    payload = mcp_service.McpAccessTokenCreate(
        scopes=["agent:invoke", "agent:invoke"],
        expires_in=900,
    )

    assert payload.scopes == ["agent:invoke"]
    assert payload.expires_in == 900

    long_lived_payload = mcp_service.McpAccessTokenCreate(
        scopes=["agent:invoke"],
        expires_in=30 * 24 * 60 * 60,
    )
    assert long_lived_payload.expires_in == 30 * 24 * 60 * 60

    with pytest.raises(ValueError):
        mcp_service.McpAccessTokenCreate(scopes=["agent:invoke"], expires_in=299)

    with pytest.raises(ValueError):
        mcp_service.McpAccessTokenCreate(scopes=["agent:invoke"], expires_in=30 * 24 * 60 * 60 + 1)

    with pytest.raises(ValueError):
        mcp_service.McpAccessTokenCreate(scopes=["unknown:scope"], expires_in=900)


def test_mcp_service_desk_exposes_dedicated_config_tab():
    view = Path("frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "type Tab = 'overview' | 'guide' | 'config' | 'clients' | 'methods'" in view
    assert "label: '服务配置'" in view
    assert "label: '使用指南'" in view
    assert "activeTab === 'config'" in view


def test_mcp_service_router_exposes_overview_clients_and_methods():
    paths = {getattr(route, "path", None) for route in mcp_service.router.routes}

    assert "/overview" in paths
    assert "/config" in paths
    assert "/clients" in paths
    assert "/clients/{client_id}" in paths
    assert "/clients/{client_id}/user-access-token" in paths
    assert "/methods" in paths


def test_client_delete_is_soft_delete_and_revokes_credentials():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert '@router.delete("/clients/{client_id}")' in source
    assert 'client.status = "deleted"' in source
    assert "McpOAuthAccessToken" in source
    assert "McpOAuthRefreshToken" in source
    assert "McpOAuthGrant" in source
    assert 'status="revoked"' in source
    assert 'McpOAuthClient.status != "deleted"' in source


def test_mcp_service_exposes_inbound_audit_query_with_read_permission_and_user_scope():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    paths = {getattr(route, "path", None) for route in mcp_service.router.routes}
    assert "/audit" in paths
    assert "McpInboundAuditLog" in source
    assert "element:mcp_service:audit:read" in source
    assert "request_id" in source
    assert "result_status" in source
    audit_segment = source.split('@router.get("/audit")', 1)[1].split('@router.get("/clients")', 1)[0]
    assert "client_secret" not in audit_segment
    assert 'if user.get("role") == "admin":' in audit_segment
    assert "McpInboundAuditLog.user_id == current_user_id" in audit_segment
    assert "owner_client_ids = select(McpOAuthClient.client_id)" not in audit_segment


def test_mcp_service_exposes_permission_scoped_audit_summary():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert 'async def audit_summary(' in source
    assert '"/audit/summary"' in source
    assert 'element:mcp_service:audit:read' in source
    assert 'Literal["24h", "7d", "30d"]' in source
    for field in ("total_calls", "success_rate", "failed_or_denied", "average_latency_ms", "p95_latency_ms"):
        assert f'"{field}"' in source


def test_mcp_service_supports_security_audit_and_time_range_queries():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert 'async def list_security_audit(' in source
    assert '"/audit/security"' in source
    assert "start_at: datetime | None" in source
    assert "end_at: datetime | None" in source
    assert "McpOAuthSecurityAuditLog" in source
    assert "event_type" in source


def test_mcp_service_exposes_audit_trend_aggregation():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert 'async def audit_trend(' in source
    assert '"/audit/trend"' in source
    assert "bucket" in source
    assert "completed" in source
    assert "denied" in source


def test_admin_can_manage_global_clients_and_client_owner_is_serialized():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "owner_user_name" in source
    assert "owner_real_name" in source
    assert 'user.get("role") == "admin"' in source
    assert "select(User)" in source


def test_audit_scope_is_independent_from_client_ownership():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    audit_segment = source.split('@router.get("/audit")', 1)[1].split('@router.get("/clients")', 1)[0]
    assert "McpOAuthClient.created_by == current_user_id" not in audit_segment
    assert "McpInboundAuditLog.user_id == current_user_id" in audit_segment


def test_audit_serializer_returns_business_fields_without_credentials():
    from datetime import datetime
    from types import SimpleNamespace

    payload = mcp_service._serialize_audit_log(SimpleNamespace(
        id="audit-1",
        request_id="req-1",
        client_request_id=None,
        client_id="client-1",
        user_id="user-1",
        auth_type="user_delegated",
        method_name="agent.invoke",
        agent_id="agent-1",
        conversation_id="conv-1",
        dataset_id=None,
        scopes=["agent:invoke"],
        status_code=200,
        result_status="completed",
        error_code=None,
        latency_ms=12,
        created_at=datetime(2026, 9, 2, 12, 0, 0),
    ))

    assert payload["request_id"] == "req-1"
    assert payload["user_id"] == "user-1"
    assert payload["scopes"] == ["agent:invoke"]
    assert "access_token" not in payload
    assert "client_secret" not in payload
    assert "authorization" not in payload


def test_mcp_service_runtime_dependencies_are_imported():
    assert hasattr(mcp_service, "settings")
    assert hasattr(mcp_service, "McpOAuthAccessToken")


def test_client_list_exposes_last_token_issue_metadata_without_token_value():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "has_issued_token" in source
    assert "last_token_issued_at" in source
    assert "last_token_issue_method" in source
    assert "manual_user_token" in source
    assert "oauth_authorization" in source
    assert '"access_token"' not in source[source.index("def _serialize_client"):source.index("@router.get(\"/overview\")")]


def test_client_registration_requires_user_authorization_code():
    with pytest.raises(ValueError, match="不支持"):
        mcp_service.McpOAuthClientCreate(
            client_name="CRM 后台任务",
            redirect_uris=[],
            allowed_grant_types=["client_credentials"],
            allowed_scopes=["agent:list"],
        )

    client = mcp_service.McpOAuthClientCreate(
        client_name="CRM 用户授权",
        redirect_uris=[],
        allowed_grant_types=["authorization_code"],
        allowed_scopes=["agent:list"],
    )
    assert client.redirect_uris == ["https://localhost/oauth/callback"]


def test_client_security_changes_revoke_existing_grants_and_tokens():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "McpOAuthGrant" in source
    assert "allowed_scopes" in source
    assert "McpOAuthAccessToken" in source
    assert "McpOAuthRefreshToken" in source
    assert "revoked_at" in source


def test_service_desk_does_not_expose_client_resource_whitelist():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    model = Path("app/models/platform_mcp.py").read_text(encoding="utf-8")
    oauth = Path("app/services/mcp/platform_oauth.py").read_text(encoding="utf-8")
    runtime = Path("app/services/mcp/platform_mcp.py").read_text(encoding="utf-8")
    frontend = Path("frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "element:mcp_service:config:read" in source
    for field in (
        "allowed_agent_ids",
        "allowed_knowledge_base_ids",
        "allowed_metadata_dataset_ids",
    ):
        assert field not in source
        assert field not in model
        assert field not in oauth
        assert field not in runtime
        assert field not in frontend
    assert "当前用户角色和权限" in frontend
    assert "Client 仅控制 MCP 方法 Scope" in frontend


def test_client_payload_rejects_legacy_resource_whitelist_fields():
    with pytest.raises(ValueError):
        mcp_service.McpOAuthClientCreate(
            client_name="legacy-client",
            redirect_uris=["https://example.com/callback"],
            allowed_scopes=["agent:list"],
            allowed_agent_ids=["agent-1"],
        )
