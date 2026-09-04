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


def test_service_desk_copy_actions_use_http_compatible_clipboard_helper():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "import { copyToClipboard } from '../utils/clipboard'" in view
    assert "await copyToClipboard(value)" in view
    assert "await navigator.clipboard.writeText(value)" not in view
    assert "复制失败，请手动复制" in view


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


def test_audit_tab_explains_role_based_visibility():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "管理员可查看全部 MCP 入站调用记录" in view
    assert "其他用户仅能查看自己发起的调用记录" in view


def test_audit_filters_are_collapsed_by_default_and_use_one_dynamic_row():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "const showAuditFilters = ref(false)" in view
    assert "展开筛选" in view
    assert "收起筛选" in view
    assert 'v-if="showAuditFilters"' in view
    assert "overflow-x-auto" in view
    assert "flex-nowrap" in view
    assert "auditFilterOptions" in view
    assert "selectedAuditFilter" in view
    assert "selectedAuditFilterValue" in view
    assert "过滤对象" in view
    assert "过滤值" in view
    assert 'class="mt-3 space-y-3"' in view


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


def test_client_card_shows_last_token_issue_time_and_method():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "尚未生成 Access Token" in view
    assert "最近签发" in view
    assert "OAuth 用户授权" in view
    assert "服务台手动生成" in view


def test_service_desk_exposes_token_management_client_filters_and_audit_export():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "Token 管理" in view
    assert "撤销" in view
    assert "clientSearch" in view
    assert "clientStatus" in view
    assert "导出 CSV" in view
    assert "securityAlert" in view
    assert "调用限流" in view


def test_audit_trend_bars_have_explicit_height_and_render_pixels():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "const trendBarHeight" in view
    assert "class=\"flex h-20 w-full items-end\"" in view
    assert "height: trendBarHeight(item.total)" in view


def test_reset_secret_is_rendered_inside_the_matching_client_card():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "secretRevealClientId" in view
    assert "secretRevealClientId === client.client_id" in view
    assert "Client Secret 已重置，请立即复制保存" in view
    assert "oneTimeSecret && !secretRevealClientId" in view


def test_client_cards_prioritize_primary_action_and_collapse_low_frequency_actions():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "clientActionMenuId" in view
    assert "更多操作" in view
    assert "有效 Token" in view
    assert "最近过期" in view
    assert "管理员视角：查看全部用户的 Client" in view
    assert "创建第一个 Client" in view
    assert "md:grid-cols-2" in view
    assert "expandedClientIds" in view
    assert "const expandedClientIds = ref<Set<string>>(new Set())" in view
    assert "收起详情" in view
    assert "展开详情" in view
    assert "最近签发" in view
    assert "有效 Token 数量" in view


def test_client_card_shows_remaining_token_days_next_to_latest_expiry():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "const remainingTokenDays" in view
    assert "还剩" in view
    assert "已过期" in view
    assert "remainingTokenDays(client.latest_token_expires_at)" in view


def test_mcp_timestamps_without_offset_are_parsed_as_utc_for_expiry_checks():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "const parseMcpTimestamp" in view
    assert "parseMcpTimestamp(token.expires_at)" in view
    assert "parseMcpTimestamp(value)" in view
    assert "${isoValue}Z" in view


def test_client_search_filters_are_collapsed_by_default():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "showClientFilters = ref(false)" in view
    assert "展开筛选" in view
    assert "收起筛选" in view
    assert 'v-if="showClientFilters"' in view


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


def test_service_desk_places_usage_guide_after_audit_tab():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert view.index("label: '审计日志'") < view.index("label: '使用指南'")


def test_service_desk_shows_permission_scoped_audit_summary_on_overview():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "auditSummary" in view
    assert "auditSummaryRange" in view
    assert "/api/portal/mcp-service/audit/summary" in view
    assert "24 小时" in view
    assert "7 天" in view
    assert "30 天" in view
    assert "调用次数" in view
    assert "成功率" in view
    assert "失败 / 拒绝" in view
    assert "P95 耗时" in view


def test_service_desk_exposes_security_audit_time_filters_and_trend():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "/api/portal/mcp-service/audit/security" in view
    assert "/api/portal/mcp-service/audit/trend" in view
    assert "start_at" in view
    assert "end_at" in view
    assert "OAuth 安全事件" in view
    assert "调用趋势" in view


def test_client_more_actions_exposes_usage_analytics_modal():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]
    assert "使用统计" in clients_section
    assert "openClientUsage" in view
    assert "/api/portal/mcp-service/clients/${encodeURIComponent(client.client_id)}/usage" in view
    for field in ("daily_trend", "method_distribution", "status_distribution", "auth_distribution", "user_distribution", "resource_distribution"):
        assert field in view


def test_client_usage_modal_supports_ranges_loading_error_and_scroll():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    assert "clientUsageRange" in view
    assert "近 7 天" in view
    assert "近 30 天" in view
    assert "近 90 天" in view
    assert "使用统计" in view
    assert "重新加载" in view
    assert "当前周期暂无调用数据" in view
    assert "max-h-[calc(100vh-2rem)]" in view
    assert "overflow-y-auto" in view
    assert "clientUsage.summary.completed_calls" in view


def test_client_usage_user_ranking_displays_account_identity_instead_of_only_id():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    usage_modal = view.split('v-if="showClientUsage && clientUsageTarget"', 1)[1]
    user_ranking = usage_modal.split("用户调用排行", 1)[1].split("资源关联排行", 1)[0]
    assert "item.display_name" in user_ranking
    assert "item.user_name" in user_ranking
    assert "user_id=" in user_ranking


def test_client_usage_kpis_fit_one_row_on_large_screens_with_compact_cards():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    usage_modal = view.split('v-if="showClientUsage && clientUsageTarget"', 1)[1]
    assert "grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" in usage_modal
    assert "text-[11px] font-bold" in usage_modal
    assert "mt-1 text-lg font-black" in usage_modal


def test_client_usage_kpis_include_six_compact_inline_svg_icons():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    usage_modal = view.split('v-if="showClientUsage && clientUsageTarget"', 1)[1]
    kpi_grid = usage_modal.split('<div class="grid gap-5 lg:grid-cols-2">', 1)[0]
    assert kpi_grid.count("<svg") == 6
    assert kpi_grid.count('aria-hidden="true"') == 6
    assert kpi_grid.count("h-4 w-4 shrink-0") == 6


def test_client_usage_trend_defaults_to_the_latest_day_after_rendering():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    assert "nextTick" in view
    assert "clientUsageTrendRef" in view
    assert "scrollLeft = clientUsageTrendRef.value.scrollWidth" in view
    assert 'ref="clientUsageTrendRef"' in view


def test_client_usage_trend_items_keep_intrinsic_width_for_horizontal_scrolling():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    trend = view.split('ref="clientUsageTrendRef"', 1)[1].split('</div>', 1)[0]
    assert "min-w-8 flex-none" in trend


def test_client_usage_trend_scrolls_after_loading_branch_mounts():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    load_usage = view.split("const loadClientUsage = async () =>", 1)[1].split("const openClientUsage", 1)[0]
    assert "clientUsageLoading.value = false\n    if (clientUsage.value) {\n      await nextTick()" in load_usage


def test_service_desk_displays_client_owner_identity_for_global_admin_list():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "owner_real_name" in view
    assert "owner_user_name" in view
    assert "所属用户" in view


def test_service_desk_can_issue_current_user_token_and_explain_dynamic_oauth():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "生成 MCP Access Token" in view
    assert "showTokenIssue" in view
    assert "expires_in" in view
    assert "user-access-token" in view
    assert "动态获取" in view
    assert "当前登录用户" in view
    assert "[2592000, '30 天']" in view
    assert "[604800, '7 天']" in view
    assert "[1296000, '15 天']" in view
    assert "最长 30 天" in view


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


def test_service_desk_guide_uses_generic_current_user_identity_wording():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "代表当前登录用户本人" in view
    assert "管理员登录生成管理员身份，demo 用户登录生成 demo 身份" not in view


def test_service_desk_exposes_resource_whitelist_editors():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "agent:list" in view
    assert "agent:invoke" in view
    assert "当前已发布，需用户授权" not in view
    assert "（待接入）" not in view
    assert "allowed_agent_ids" in view
    assert "allowed_knowledge_base_ids" in view
    assert "allowed_metadata_dataset_ids" in view
    assert "编辑智能体白名单" in view
    assert "编辑知识库白名单" in view
    assert "编辑数据集白名单" in view
    assert "resource-options" in view
    assert 'type="checkbox"' in view
    assert "跟随用户权限" in view
    assert "仅允许指定资源" in view
    assert "勾选当前搜索结果" in view
    assert "取消限制，跟随用户权限" in view


def test_service_desk_resource_whitelist_save_is_field_scoped():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "resourceWhitelistModal.field" in view
    assert "resourceWhitelistModal.selectedIds" in view
    assert "{ [resourceWhitelistModal.field]: value }" in view
    assert "await loadClients()" in view
    assert "确定禁止该 Client 访问全部" in view
    assert "resourceWhitelistConfirm" in view
    assert "confirmResourceWhitelistSave" in view


def test_resource_whitelist_buttons_are_visible_without_expanding_client_details():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]
    expanded_details = clients_section.index('<div v-if="expandedClientIds.has(client.client_id)"')

    assert "resourceWhitelistConfigs" in clients_section
    assert "{{ config.buttonLabel }}" in clients_section
    assert "openResourceWhitelist(client, config)" in clients_section
    assert "resourcePolicyButtonClass(client, config.field)" in clients_section
    assert "bg-emerald-50" in view
    assert "bg-amber-50" in view
    assert "bg-rose-50" in view


def test_resource_whitelist_save_shows_success_feedback_and_supports_hidden_summaries():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]

    assert "showToast('资源白名单已更新', 'success')" in view
    assert "resource_policy_summary" in view
    assert "资源策略详情不可见" in view
    assert ':title="resourcePolicySummary(client, config.field)"' in clients_section
    assert "创建 Client 后，可在 Client 卡片的资源访问区域配置白名单" in view


def test_service_desk_exposes_only_user_bound_oauth_flow():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "authorization_code" in view
    assert "Authorization Code + PKCE" in view
    assert "authorizationCodePython" in view
    assert "client_credentials" not in view
    assert "Client Credentials" not in view


def test_service_desk_documents_default_redirect_uri_for_empty_input():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "https://localhost/oauth/callback" in view
    assert "未填写时使用默认回调地址" in view


def test_client_card_labels_and_copies_client_id():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "Client ID" in view
    assert "client-id-" in view
    assert "Client ID 用于 Token Endpoint" in view
    assert "复制 Client ID" in view


def test_client_token_action_uses_soft_primary_treatment_while_create_remains_primary():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    client_actions = view.split('data-testid="client-actions"', 1)[1]
    token_button = client_actions.split('@click="openTokenIssue(client)"', 1)[0].rsplit("<button", 1)[1]
    create_button = view.split('@click="showCreate = true"', 1)[0].rsplit("<button", 1)[1]

    assert "border-indigo-200" in token_button
    assert "bg-indigo-50" in token_button
    assert "text-indigo-700" in token_button
    assert "bg-indigo-600" not in token_button
    assert "bg-indigo-600" in create_button


def test_client_card_uses_compact_permission_summary_and_on_demand_details():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert 'class="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-bold text-slate-600"' in view
    assert "权限摘要" in view
    assert "查看权限详情" in view
    assert "md:grid-cols-2" in view
    assert "scopeSummary" in view
    assert "资源权限按“当前用户权限 ∩ Client 白名单”生效" in view
    assert "Client 白名单" in view

    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]
    assert "inline-flex max-w-full items-center gap-1 rounded-full" not in clients_section
    assert "不增加额外限制（用户模式仍受用户权限限制）" not in clients_section


def test_client_status_is_present_in_card_footer_separate_from_action_buttons():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    card = view.split('<div v-for="client in clients"', 1)[1]

    actions_start = card.index('data-testid="client-actions"')
    status_start = card.index('data-testid="client-status"')
    status_end = card.index('</div>', status_start) + len('</div>')
    status_block = card[status_start:status_end]
    assert status_start > actions_start
    assert 'class="mt-3 flex items-center justify-end gap-2 text-xs"' in card
    assert 'aria-label="Client 状态"' in card
    assert '<button' not in status_block
    assert '@click' not in status_block


def test_client_detail_toggle_uses_icon_button_with_accessibility_label():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert ":aria-label=\"expandedClientIds.has(client.client_id) ? '收起 Client 详情' : '展开 Client 详情'\"" in view
    assert ":title=\"expandedClientIds.has(client.client_id) ? '收起详情' : '展开详情'\"" in view
    assert ":aria-controls=\"'client-details-' + client.client_id\"" in view
    assert "@click=\"toggleClientExpanded(client.client_id)\"" in view
    assert "<svg" in view
    assert "展开详情</button>" not in view


def test_shared_client_mutations_are_owner_scoped_in_ui():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]

    assert "const { hasPermission, isAdmin, userInfo } = useUser()" in view
    assert "const isClientOwner = (client: Client) =>" in view
    assert "!!currentUserId.value" in view
    assert "const canManageClientItem = (client: Client) =>" in view
    assert "String(client.created_by ?? '') === currentUserId.value" in view
    assert 'v-if="canManageClientItem(client)"' in clients_section
    assert 'v-if="canResetSecretForClient(client)"' in clients_section
    assert "canRevokeAllClientTokens(tokenDetailsClient)" in view


def test_client_edit_save_force_closes_after_saving():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "const closeClientEdit = (force = false)" in view
    assert "closeClientEdit(true)" in view


def test_client_scope_can_be_edited_from_client_card():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "编辑 Scope" in view
    assert "showClientScopeEdit" in view
    assert "clientScopeEditForm" in view
    assert "保存 Scope" in view
    assert "allowed_scopes: clientScopeEditForm.scopes" in view
    assert "Scope 变更会让该 Client 已有的 Access Token 和授权关系失效" in view


def test_client_scope_change_shows_reissue_guidance_until_new_token_is_issued():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "needs_token_regeneration" in view
    assert "当前 Client 需要重新生成 MCP Access Token" in view
    assert "立即生成" in view
    assert "await loadClients()" in view
    assert "client.needs_token_regeneration" in view
    assert "当前 Client 需要重新生成 MCP Access Token" in view


def test_client_reissue_guidance_is_visible_when_card_is_collapsed():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    card_start = view.index('<div v-for="client in clients"')
    header_start = view.index('<div class="flex flex-col gap-4', card_start)
    alert_start = view.index('v-if="client.needs_token_regeneration"', card_start)
    expanded_start = view.index('v-if="expandedClientIds.has(client.client_id)"', card_start)
    assert alert_start < header_start or alert_start < expanded_start
    assert "原有 Access Token 已失效，请重新生成 MCP Access Token" in view


def test_unchanged_client_scope_shows_toast_without_saving():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    assert "Scope 未变化，Client Secret 和 Access Token 均未变化" in view
    assert "JSON.stringify(currentScopes) === JSON.stringify(nextScopes)" in view
    assert "closeClientScopeEdit(true)" in view


def test_reset_secret_refreshes_client_state_for_reissue_guidance():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    reset_branch = view.split("action === 'reset-secret'", 1)[1].split("} else", 1)[0]
    assert "await loadClients()" in reset_branch


def test_client_list_does_not_repeat_manual_token_guidance_banner():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    clients_section = view.split("activeTab === 'clients'", 1)[1].split("activeTab === 'methods'", 1)[0]

    assert "人工登录接入：" not in clients_section


def test_service_desk_exposes_client_token_lifecycle_summary_and_physical_delete_ui():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")

    for field in (
        "token_total_count",
        "expiring_token_count",
        "expired_token_count",
        "revoked_token_count",
    ):
        assert field in view
    assert "即将过期" in view
    assert "selectedTokenIds" in view
    assert "tokenStatusFilter" in view
    assert "filteredClientTokens" in view
    assert "物理删除" in view
    assert "/tokens/delete" in view
    assert "删除已选 Token" in view
    assert "getTokenStatus(token) === 'active' && canDeleteClientToken(token)" in view
    assert "const getTokenStatus = (token: ClientToken)" in view
    assert "tokenClock.value = Date.now()" in view


def test_token_lifecycle_table_hides_authorized_user_column():
    view = (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")
    token_table = view.split('<h2 class="text-xl font-black">Token 生命周期</h2>', 1)[1].split('<!-- Client 基本信息编辑弹窗 -->', 1)[0]

    assert "授权用户" not in token_table
    assert '<th class="p-3">时间信息</th>' in token_table
    assert '<th class="p-3">生成时间</th>' not in token_table
    assert '<th class="p-3">过期时间</th>' not in token_table
    for heading in ("Scope 范围", "生成方式", "状态", "操作"):
        assert heading in token_table
    assert "生成：" in token_table
    assert "过期：" in token_table
    assert "tokenRemainingLabel(token)" in token_table
    assert "const tokenRemainingLabel" in view
    assert "还剩 ${days} 天" in view


def test_login_reloads_backend_oauth_endpoint_after_same_origin_login():
    login = (ROOT / "frontend/src/views/Login.vue").read_text(encoding="utf-8")

    assert "window.location.assign(returnPath)" in login
    assert "oauth/authorize" in login
