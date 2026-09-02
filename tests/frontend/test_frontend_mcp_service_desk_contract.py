from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


ROOT = Path(__file__).parents[2]


def test_mcp_service_desk_has_independent_route_menu_and_read_only_copy_fields():
    router = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
    dashboard = (ROOT / "frontend/src/views/Dashboard.vue").read_text(encoding="utf-8")
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "mcp-service" in router
    assert "menu:mcp_service" in router
    assert "MCP 服务台" in dashboard
    assert "复制" in view
    assert "/api/portal/mcp-service/overview" in view
    assert "element:mcp_service:capability:manage" in view
    assert "element:mcp_service:config:edit" in view


def test_mcp_toolkit_route_remains_outbound_route():
    router = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")

    assert "McpManagement" in router
    assert "menu:mcp_management" in router
    assert "McpServiceDesk" in router


def test_service_desk_hides_tabs_and_disables_switches_without_read_permission():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "label: '服务配置'" in view
    assert "activeTab === 'config'" in view
    assert "canReadOverview" in view
    assert "canReadClients" in view
    assert "canReadMethods" in view
    assert "availableTabs" in view
    assert "!canReadConfig" in view


def test_service_desk_uses_the_same_dashboard_spacing_and_background_as_mcp_toolkit():
    toolkit = (ROOT / "frontend/src/views/McpManagement.vue").read_text(encoding="utf-8")
    service_desk = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    dashboard = (ROOT / "frontend/src/views/Dashboard.vue").read_text(encoding="utf-8")

    assert 'class="flex flex-col space-y-4"' in toolkit
    assert "bg-gray-100 custom-scrollbar" in dashboard
    assert 'class="flex min-h-full flex-col space-y-4 text-slate-800"' in service_desk
    assert "bg-slate-50 px-5 py-6" not in service_desk
    assert "mx-auto max-w-7xl space-y-6" not in service_desk


def test_service_desk_configuration_cards_expose_clear_capsule_switches():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert 'role="switch"' in view
    assert ':aria-checked="config[item[0]] === true"' in view
    assert "h-6 w-11" in view
    assert "translate-x-5" in view
    assert "已开启" in view
    assert "已关闭" in view


def test_login_reloads_backend_oauth_endpoint_after_same_origin_login():
    login = (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")

    assert "window.location.assign(returnPath)" in login
    assert "oauth/authorize" in login
