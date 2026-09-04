from pathlib import Path
from types import SimpleNamespace

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
    assert "McpOAuthAccessToken.revoked_at" in source
    assert "McpOAuthAccessToken.expires_at" in source


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


def test_mcp_service_exposes_client_usage_analytics_with_audit_scope():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    paths = {getattr(route, "path", None) for route in mcp_service.router.routes}
    assert "/clients/{client_id}/usage" in paths
    assert 'async def client_usage(' in source
    assert 'range: Literal["7d", "30d", "90d"]' in source
    assert "element:mcp_service:audit:read" in source
    usage_segment = source.split('@router.get("/clients/{client_id}/usage")', 1)[1].split("@router.get", 1)[0]
    assert "McpInboundAuditLog.client_id == client_id" in usage_segment
    assert "McpInboundAuditLog.user_id == current_user_id" in usage_segment
    for field in (
        '"summary"', '"daily_trend"', '"method_distribution"',
        '"status_distribution"', '"auth_distribution"',
        '"user_distribution"', '"resource_distribution"',
        '"p95_latency_ms"', '"active_user_count"',
    ):
        assert field in usage_segment


def test_client_usage_analytics_never_serializes_credentials_or_request_payload():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    usage_segment = source.split('@router.get("/clients/{client_id}/usage")', 1)[1].split("@router.get", 1)[0]
    assert "access_token" not in usage_segment
    assert "client_secret" not in usage_segment
    assert "request_headers" not in usage_segment
    assert "tool_input" not in usage_segment
    assert "tool_output" not in usage_segment


def test_client_usage_analytics_uses_database_aggregates_instead_of_loading_all_details():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    usage_segment = source.split('@router.get("/clients/{client_id}/usage")', 1)[1].split("@router.get", 1)[0]
    assert "func.count()" in usage_segment
    assert ".group_by(" in usage_segment
    assert "select(McpInboundAuditLog.latency_ms)" in usage_segment


def test_client_usage_summary_exposes_completed_calls_explicitly():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    usage_segment = source.split('@router.get("/clients/{client_id}/usage")', 1)[1].split("@router.get", 1)[0]
    assert '"completed_calls"' in usage_segment


def test_client_usage_user_distribution_includes_account_identity_fields():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    usage_segment = source.split('@router.get("/clients/{client_id}/usage")', 1)[1].split("@router.get", 1)[0]
    assert "select(User.id, User.user_name, User.real_name)" in usage_segment
    for field in ('"display_name"', '"user_name"', '"real_name"'):
        assert field in usage_segment


def test_usage_date_range_returns_complete_natural_day_buckets():
    start_at, end_at, dates = mcp_service._usage_date_range("7d")
    assert len(dates) == 7
    assert dates[0] == start_at.date().isoformat()
    assert dates[-1] == end_at.date().isoformat()
    assert start_at.hour == 0
    assert end_at.hour == 23


def test_usage_resource_counts_keeps_multiple_resource_dimensions_and_other():
    items = mcp_service._serialize_usage_resource_distribution([
        ("agent", "agent-1", 1),
        ("conversation", "conv-1", 1),
    ], other_total=1)
    assert {item["type"] + ":" + item["name"] for item in items} == {
        "agent:agent-1", "conversation:conv-1", "other:其他"
    }


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


def test_service_desk_exposes_token_lifecycle_and_client_query_endpoints():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert '"/clients/{client_id}/tokens"' in source
    assert '"/clients/{client_id}/tokens/{token_id}/revoke"' in source
    assert "token_id" in source
    assert "revoked_at" in source
    assert "client_name" in source
    assert "created_by" in source
    assert "active_token_count" in source
    assert "latest_token_expires_at" in source


def test_mcp_oauth_timestamps_are_serialized_with_explicit_utc_offset():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "def _serialize_mcp_datetime" in source
    assert "replace(\"+00:00\", \"Z\")" in source
    assert "_serialize_mcp_datetime(latest_token_expires_at)" in source
    assert "_serialize_mcp_datetime(token.expires_at)" in source


def test_client_token_management_exposes_lifecycle_counts_and_physical_delete_routes():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    for field in (
        "token_total_count",
        "expiring_token_count",
        "expired_token_count",
        "revoked_token_count",
    ):
        assert field in source
    assert '@router.delete("/clients/{client_id}/tokens/{token_id}")' in source
    assert '@router.post("/clients/{client_id}/tokens/delete")' in source
    assert "delete(McpOAuthAccessToken)" in source
    delete_segment = source.split('@router.delete("/clients/{client_id}/tokens/{token_id}")', 1)[1].split("\n@router", 1)[0]
    batch_delete_segment = source.split('@router.post("/clients/{client_id}/tokens/delete")', 1)[1].split("\n@router", 1)[0]
    for segment in (delete_segment, batch_delete_segment):
        assert "McpOAuthGrant" not in segment
        assert "McpOAuthRefreshToken" not in segment
        assert "McpOAuthAccessToken" in segment
    assert ".where(*filters)" in source[source.index("async def list_client_tokens"):source.index('@router.delete("/clients/{client_id}/tokens/{token_id}")')]
    assert ".limit(100)" not in source[source.index("async def list_client_tokens"):source.index('@router.delete("/clients/{client_id}/tokens/{token_id}")')]
    assert "oauth_access_token_deleted" in source


def test_scope_only_client_update_does_not_revalidate_unrelated_redirect_uri():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "redirect_config_changed" in source
    assert "if redirect_config_changed:" in source


def test_client_reissue_state_covers_all_invalidated_token_paths_without_false_revoke():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "needs_token_regeneration=(" in source
    assert "has_issued_token" in source
    assert "active_token_count" in source
    assert "security_changed" in source
    assert "redirect_uris_changed" in source
    assert "scope_changed or grant_types_changed or redirect_uris_changed or status_changed" in source
    assert "row.status == \"active\"" in source


def test_service_desk_exposes_audit_export_and_rate_limit_config():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    model = Path("app/models/platform_mcp.py").read_text(encoding="utf-8")

    assert '"/audit/export"' in source
    assert "text/csv" in source
    assert "rate_limit_client_per_minute" in model
    assert "rate_limit_user_per_minute" in model


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


def test_playground_does_not_return_complete_access_token():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    playground = source.split('@router.post("/playground/test")', 1)[1]

    assert '"token_masked"' in playground
    assert '"token_used"' not in playground


def test_service_desk_exposes_client_resource_whitelist_fields_across_layers():
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
        assert field in source
        assert field in model
        assert field in oauth
        assert field in runtime
        assert field in frontend
    assert "resource_policy_changed" in source
    assert "intersect_authorized_ids" in runtime


def test_client_payload_accepts_resource_whitelist_fields_and_preserves_three_states():
    unrestricted = mcp_service.McpOAuthClientCreate(
        client_name="resource-policy-client",
        redirect_uris=["https://example.com/oauth/callback"],
        allowed_scopes=["agent:list"],
        allowed_agent_ids=None,
        allowed_knowledge_base_ids=None,
        allowed_metadata_dataset_ids=None,
    )
    empty = mcp_service.McpOAuthClientUpdate(allowed_agent_ids=[])
    selected = mcp_service.McpOAuthClientUpdate(
        allowed_knowledge_base_ids=[" kb-1 ", "kb-1", "kb-2"]
    )

    assert unrestricted.allowed_agent_ids is None
    assert unrestricted.allowed_knowledge_base_ids is None
    assert unrestricted.allowed_metadata_dataset_ids is None
    assert empty.allowed_agent_ids == []
    assert selected.allowed_knowledge_base_ids == ["kb-1", "kb-2"]


def test_client_payload_rejects_unknown_resource_policy_fields():
    with pytest.raises(ValueError):
        mcp_service.McpOAuthClientCreate(
            client_name="unknown-policy-client",
            redirect_uris=["https://example.com/callback"],
            allowed_scopes=["agent:list"],
            allowed_resource_ids=["resource-1"],
        )


def test_client_payload_rejects_non_array_resource_policy_values():
    with pytest.raises(ValueError, match="必须是数组或 null"):
        mcp_service.McpOAuthClientUpdate(allowed_agent_ids="agent-1")

    with pytest.raises(ValueError, match="必须是字符串"):
        mcp_service.McpOAuthClientUpdate(allowed_agent_ids=["agent-1", 2])


def test_resource_policy_validation_rejects_ids_outside_accessible_resources():
    assert mcp_service._validate_resource_ids_are_accessible(
        ["agent-1"],
        {"agent-1", "agent-2"},
        "allowed_agent_ids",
    ) == ["agent-1"]

    with pytest.raises(ValueError, match="无效或无权限"):
        mcp_service._validate_resource_ids_are_accessible(
            ["not-existing"],
            {"agent-1"},
            "allowed_agent_ids",
        )


def test_client_serialization_hides_resource_ids_but_keeps_policy_summary():
    client = SimpleNamespace(
        id="db-id",
        client_id="client-id",
        client_name="client",
        client_type="confidential",
        redirect_uris=[],
        allowed_grant_types=["authorization_code"],
        allowed_scopes=["agent:list"],
        allowed_agent_ids=["agent-1", "agent-2"],
        allowed_knowledge_base_ids=[],
        allowed_metadata_dataset_ids=None,
        scope_version=1,
        is_shared=True,
        status="active",
        created_by="1",
        created_at=None,
        updated_at=None,
        disabled_at=None,
    )

    serialized = mcp_service._serialize_client(client, include_resource_details=False)

    assert "allowed_agent_ids" not in serialized
    assert "allowed_knowledge_base_ids" not in serialized
    assert "allowed_metadata_dataset_ids" not in serialized
    assert serialized["resource_policy_summary"] == {
        "allowed_agent_ids": {"mode": "restricted", "count": 2},
        "allowed_knowledge_base_ids": {"mode": "none", "count": 0},
        "allowed_metadata_dataset_ids": {"mode": "unrestricted", "count": None},
    }


def test_client_list_uses_owner_scoped_resource_serialization_and_invalidates_codes():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "include_resource_details" in source
    assert "row.created_by" in source
    assert "invalidate_unconsumed_authorization_codes" in source
    assert "resource_policy_changed" in source


def test_resource_policy_audit_records_types_and_counts_without_ids():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    audit_segment = source.split('event_type="client_updated"', 1)[1].split("await db.commit()", 1)[0]

    assert "resource_types" in audit_segment
    assert "resource_counts" in audit_segment
    assert "allowed_agent_ids" not in audit_segment


def test_resource_options_endpoint_uses_user_scoped_sources_and_pagination():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert 'resource_type: Literal["agent", "knowledge_base", "metadata_dataset"]' in source
    assert "AgentManagerService.list_allowed_agents" in source
    assert "get_knowledge_base_access" in source
    assert "list_accessible_dataset_options" in source
    assert '"resource_type": resource_type' in source
    assert '"items": items' in source
    assert '"has_more":' in source


def test_resource_whitelist_changes_revoke_tokens_and_grants():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "resource_policy_changed" in source
    assert "McpOAuthAccessToken.revoked_at" in source
    assert "McpOAuthRefreshToken.revoked_at" in source
    assert "McpOAuthGrant.status" in source
