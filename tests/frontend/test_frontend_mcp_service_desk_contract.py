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


def test_service_desk_exposes_permission_gated_audit_tab_and_filters():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "'audit'" in view
    assert "label: '审计日志'" in view
    assert "element:mcp_service:audit:read" in view
    assert "/api/portal/mcp-service/audit" in view
    assert "request_id" in view
    assert "method_name" in view
    assert "result_status" in view
    assert "查看详情" in view


def test_audit_filters_are_collapsed_by_default_and_use_one_dynamic_row():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "const showAuditFilters = ref(false)" in view
    assert "展开筛选" in view
    assert "收起筛选" in view
    assert 'v-if="showAuditFilters"' in view
    assert "flex-nowrap" in view
    assert "auditFilterOptions" in view
    assert "selectedAuditFilter" in view
    assert "selectedAuditFilterValue" in view
    assert "过滤对象" in view
    assert "过滤值" in view
    assert 'mt-3 flex flex-nowrap items-end gap-3 overflow-x-auto pb-1' not in view


def test_client_delete_requires_confirmation_and_keeps_audit_history():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "ClientConfirmAction = 'disable' | 'reset-secret' | 'delete'" in view
    assert "确认删除 Client" in view
    assert "删除后会发生什么？" in view
    assert "审计记录会保留" in view
    assert "api.delete" in view


def test_client_token_issue_uses_primary_button_and_clear_label():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "生成 MCP Access Token" in view
    assert "生成当前用户 Access Token" not in view
    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]
    assert "bg-indigo-600" in clients_section
    assert "hover:bg-indigo-700" in clients_section
    assert "disabled:opacity-50" in clients_section


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


def test_service_desk_refresh_reuses_workbench_refresh_interaction():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "workbench-refresh-btn" in view
    assert ":disabled=\"loading\"" in view
    assert "刷新中" in view
    assert "animate-spin" in view


def test_service_overview_addresses_have_individual_help_dialogs():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "endpointHelpItems" in view
    assert "EndpointHelpKey" in view
    assert "openEndpointHelp" in view
    assert "showEndpointHelp" in view
    for key in ("endpoint", "resource", "oauth", "protected"):
        assert f"key: '{key}'" in view
    assert "MCP 请求实际发送到这里" in view
    assert "OAuth2 获取 Token 时使用" in view
    assert "发现授权服务器和资源信息" in view
    assert "复制地址" in view


def test_client_modal_is_viewport_safe_and_scope_list_can_scroll():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "max-h-[calc(100vh-2rem)]" in view
    assert "overflow-y-auto" in view
    assert "min-h-0 flex-1" in view
    assert "允许 Scope" in view
    assert "max-h-56" in view


def test_service_desk_has_oauth_usage_guide_and_copyable_mcp_json():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "type Tab = 'overview' | 'guide' | 'config' | 'clients' | 'methods' | 'audit'" in view
    assert "label: '使用指南'" in view
    assert "mcpServers" in view
    assert "NANZI_PLATFORM_MCP_ACCESS_TOKEN" in view
    assert "Authorization Code + PKCE" in view
    assert "Protected Resource Metadata" in view
    assert "copyMcpJson" in view
    assert "服务台生成的当前用户 Token" in view


def test_service_desk_can_issue_current_user_token_and_explain_dynamic_oauth():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "生成 MCP Access Token" in view
    assert "showTokenIssue" in view
    assert "expires_in" in view
    assert "user-access-token" in view
    assert "动态获取" in view
    assert "当前登录用户" in view


def test_service_desk_token_issue_uses_second_step_with_token_and_direct_mcp_json_copy():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "tokenWizardStep" in view
    assert "tokenWizardStep === 2" in view
    assert "复制 Access Token" in view
    assert "generatedMcpJson" in view
    assert "复制 MCP JSON" in view
    assert "直接粘贴" in view


def test_client_destructive_actions_require_confirmation_and_explain_token_impact():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "openClientConfirm" in view
    assert "确认停用 Client" in view
    assert "确认重置 Client Secret" in view
    assert "Access Token、Refresh Token 会立即失效" in view
    assert "旧 Client Secret 立即失效" in view
    assert "confirmClientAction" in view


def test_service_desk_guide_prioritizes_manual_low_code_usage_before_programmatic_oauth():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    manual_index = view.index("人工 / 低代码客户端")
    programmatic_index = view.index("程序化系统接入")

    assert manual_index < programmatic_index
    assert "Cursor" in view
    assert "Claude Desktop" in view
    assert "Dify" in view
    assert "登录 NanZi" in view
    assert "重新生成" in view


def test_service_desk_guide_explains_client_secret_scenarios_and_sample_code():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "Client Secret 只用于获取 Access Token" in view
    assert "始终代表完成 NanZi 登录授权的用户" in view
    assert "client_credentials" not in view
    assert "Client Credentials" not in view
    assert "curl" in view
    assert "requests.post" in view
    assert "Authorization: Bearer" in view
    assert "不要把 Client Secret 放进 Cursor" in view


def test_service_desk_client_form_exposes_method_scopes_without_resource_whitelists():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "agent:list" in view
    assert "agent:invoke" in view
    assert "当前已发布，需用户授权" not in view
    assert "（待接入）" not in view
    assert "allowed_agent_ids" not in view
    assert "allowed_knowledge_base_ids" not in view
    assert "allowed_metadata_dataset_ids" not in view
    assert "Client 仅控制 MCP 方法 Scope" in view
    assert "当前用户角色和权限" in view


def test_service_desk_exposes_only_user_bound_oauth_flow():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "authorization_code" in view
    assert "Authorization Code + PKCE" in view
    assert "authorizationCodePython" in view
    assert "client_credentials" not in view
    assert "Client Credentials" not in view


def test_client_card_labels_and_copies_client_id():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "Client ID" in view
    assert "client-id-" in view
    assert "Client ID 用于 Token Endpoint" in view
    assert "复制 Client ID" in view


def test_client_card_uses_compact_permission_summary_and_on_demand_details():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert 'class="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-bold text-slate-600"' in view
    assert "权限摘要" in view
    assert "查看权限详情" in view
    assert "md:grid-cols-2" in view
    assert "scopeSummary" in view
    assert "资源权限：由当前登录用户的角色和权限决定" in view
    assert "Client 不再配置" in view

    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]
    assert "inline-flex max-w-full items-center gap-1 rounded-full" not in clients_section
    assert "不增加额外限制（用户模式仍受用户权限限制）" not in clients_section


def test_login_reloads_backend_oauth_endpoint_after_same_origin_login():
    login = (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")

    assert "window.location.assign(returnPath)" in login
    assert "oauth/authorize" in login
