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


def test_mcp_service_desk_exposes_dedicated_config_tab():
    view = Path("frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "type Tab = 'overview' | 'config' | 'clients' | 'methods'" in view
    assert "label: '服务配置'" in view
    assert "activeTab === 'config'" in view


def test_mcp_service_router_exposes_overview_clients_and_methods():
    paths = {getattr(route, "path", None) for route in mcp_service.router.routes}

    assert "/overview" in paths
    assert "/config" in paths
    assert "/clients" in paths
    assert "/clients/{client_id}" in paths
    assert "/methods" in paths


def test_mcp_service_runtime_dependencies_are_imported():
    assert hasattr(mcp_service, "settings")
    assert hasattr(mcp_service, "McpOAuthAccessToken")


def test_client_credentials_only_registration_does_not_need_redirect_uri():
    client = mcp_service.McpOAuthClientCreate(
        client_name="CRM 后台任务",
        redirect_uris=[],
        allowed_grant_types=["client_credentials"],
        allowed_scopes=["agent:list"],
    )

    assert client.redirect_uris == []


def test_client_security_changes_revoke_existing_grants_and_tokens():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")

    assert "McpOAuthGrant" in source
    assert "allowed_scopes" in source
    assert "McpOAuthAccessToken" in source
    assert "McpOAuthRefreshToken" in source
    assert "revoked_at" in source


def test_service_desk_exposes_client_resource_whitelist_and_explicit_read_permissions():
    source = Path("app/api/portal/endpoints/mcp_service.py").read_text(encoding="utf-8")
    frontend = Path("frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "allowed_knowledge_base_ids" in source
    assert "element:mcp_service:config:read" in source
    assert "allowed_knowledge_base_ids" in frontend
    assert "不增加额外限制" in frontend
    assert "return ids.length ? ids : null" in frontend
