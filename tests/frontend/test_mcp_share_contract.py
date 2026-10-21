"""个人 MCP 分享的前端契约：左菜单、弹窗、两个面板与接口路径必须保持连通。"""

from pathlib import Path

import pytest


FRONTEND = Path("frontend/src")
MCP_VIEW = FRONTEND / "views" / "McpManagement.vue"
REGISTRY = FRONTEND / "components" / "system" / "McpServerRegistry.vue"
PERSONAL_MENU = FRONTEND / "components" / "mcp" / "McpPersonalMenu.vue"
SHARE_DIALOG = FRONTEND / "components" / "mcp" / "McpShareDialog.vue"
RECEIVED_PANEL = FRONTEND / "components" / "mcp" / "ReceivedMcpSharesPanel.vue"
ACCEPT_WIZARD = FRONTEND / "components" / "mcp" / "McpShareAcceptWizard.vue"
SENT_PANEL = FRONTEND / "components" / "mcp" / "SentMcpSharesPanel.vue"
PERSONAL_CENTER = FRONTEND / "views" / "PersonalCenter.vue"
BELL = FRONTEND / "components" / "PortalNotificationBell.vue"


pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_mcp_view_renders_personal_share_layout():
    source = _read(MCP_VIEW)
    assert "md:flex-row" in source
    assert "McpPersonalMenu" in source
    assert 'mcpMenu === \'received\'' in source
    assert 'mcpMenu === \'sent\'' in source
    assert "McpShareDialog" in source
    assert "initialPersonalMenu" in source


def test_mcp_view_keeps_platform_tabs():
    source = _read(MCP_VIEW)
    assert "平台 MCP" in source
    assert "我的 MCP" in source
    assert "审计日志" in source


def test_personal_menu_lists_three_sections():
    source = _read(PERSONAL_MENU)
    assert "我的 MCP" in source
    assert "收到的分享" in source
    assert "我的分享" in source
    assert "variant === 'aside'" in source
    assert "hidden w-[200px]" in source
    assert "md:hidden" in source


def test_share_dialog_sends_targets_with_credential_toggle():
    source = _read(SHARE_DIALOG)
    assert "/api/portal/mcp/share-targets" in source
    assert "target_user_ids" in source
    assert ':maxlength="MAX_MESSAGE"' in source
    assert "include_credentials" in source
    assert "携带认证凭证" in source


def test_share_dialog_warns_about_shared_secrets():
    """携带凭证等于交出访问权，弹窗必须显式说明，不能静默勾选。"""
    source = _read(SHARE_DIALOG)
    assert "Authorization" in source
    assert "私钥" in source
    assert "可信同事" in source


def test_received_panel_accepts_and_rejects():
    source = _read(RECEIVED_PANEL)
    assert "/api/portal/mcp/shares/received" in source
    assert "/accept" in source
    assert "/reject" in source
    # 一键安装 + 可编辑的「自定义安装」两条路
    assert "接受并安装" in source
    assert "自定义安装" in source
    assert "McpShareAcceptWizard" in source
    assert "pending_count" in source
    assert "emit('go-mine'" in source


def test_sent_panel_filters_and_withdraws():
    source = _read(SENT_PANEL)
    assert "/api/portal/mcp/shares/sent" in source
    assert "status_counts" in source
    assert "撤回" in source
    assert "shareStatusMeta" in source


def test_registry_only_shares_personal_servers():
    source = _read(REGISTRY)
    assert "(e: 'share-server', server: any): void" in source
    assert "emit('share-server', server)" in source
    index = source.index("emit('share-server', server)")
    window = source[max(0, index - 900): index]
    assert "props.scope === 'personal'" in window


def test_registry_shows_share_lineage():
    source = _read(REGISTRY)
    assert "server.shared_from" in source
    assert "server.share_count" in source
    assert "的分享" in source


def test_share_dialog_surfaces_credential_flag_on_cards():
    received = _read(RECEIVED_PANEL)
    sent = _read(SENT_PANEL)
    assert "未携带凭证" in received
    assert "未携带凭证" in sent


def test_personal_center_forwards_mcp_tab_deep_link():
    source = _read(PERSONAL_CENTER)
    assert "mcp_tab" in source
    assert ':initial-personal-menu="mcpInitialMenu"' in source


def test_notification_bell_routes_mcp_share():
    source = _read(BELL)
    assert "mcp_share" in source
    assert "MCP 分享" in source
    assert "mcp_tab" in source


def test_share_dialog_closes_after_sending():
    """分享成功后必须关闭弹窗。

    留着弹窗（哪怕把按钮改成「完成」）都会让人怀疑「是不是还要再点一次才算发出去」，
    用户明确要求发完就关。
    """
    source = _read(SHARE_DIALOG)
    submit_body = source.split("async function submit()", 1)[1].split("</script>", 1)[0]
    assert "emit('sent')" in submit_body
    assert "emit('close')" in submit_body
    assert "sentCount" not in source


def test_share_dialog_warns_when_url_embeds_credential():
    """地址本身含令牌（ModelScope 网关）时，取消勾选也藏不住，必须提示。"""
    source = _read(SHARE_DIALOG)
    assert "urlEmbedsCredential" in source
    assert "这个地址本身含访问令牌" in source
    assert "取消勾选「携带认证凭证」也无法隐藏它" in source
    # 检测要覆盖「路径内长 hex 段」与「query 里的 token/key」
    assert "[0-9a-f]{12,}" in source
    assert "access_token" in source


def test_share_dialog_shows_account_beside_real_name():
    """接收人列表要显示「姓名@账号名」，否则同名/只有账号时认不出是谁。"""
    source = _read(SHARE_DIALOG)
    assert "@{{ user.user_name }}" in source
    assert "user.real_name !== user.user_name" in source


def test_received_panel_reports_missing_target_server():
    """接受后又把副本删掉时，「去我的 MCP 看看」不能静默无反应。"""
    registry = _read(REGISTRY)
    assert "focusServer = (serverId: string): Promise<boolean>" in registry
    assert "settlePendingFocus(false)" in registry
    view = _read(MCP_VIEW)
    assert "await registryRef.value?.focusServer(serverId)" in view
    assert "const focused = await registryRef.value?.focusServer(serverId)" in view
    assert "focused === false" in view
    assert "已经不在了" in view


def test_confirm_modals_use_v_if_instead_of_show_prop():
    """ConfirmModal 没有 show prop（靠父级 v-if 控制挂载）。

    传 :show 会被当成普通 attr 丢弃，弹窗从挂载起就常驻——用户点一下「收到的分享」
    会凭空弹出一个没有标题、没有正文的确认框。
    """
    for path in (RECEIVED_PANEL, SENT_PANEL):
        source = _read(path)
        assert ':show="confirmState' not in source, f"{path.name} 用了不存在的 show prop"
        assert 'v-if="confirmState.show"' in source, f"{path.name} 缺少 v-if 控制"


def test_accept_wizard_prefills_and_submits_overrides():
    """安装向导弹层要预填落地名/备注/地址，提交时把用户改过的值带上。"""
    source = _read(ACCEPT_WIZARD)
    assert "suggested_server_name" in source
    assert "stripMcpServerNamePrefix" in source
    assert "composeMcpServerName" in source
    for field in ("server_name", "remark", "sse_url"):
        assert field in source, f"安装向导缺少 {field} 覆盖"
    assert "shares/received/${props.share.id}/accept" in source


def test_accept_wizard_validates_url_before_submit():
    source = _read(ACCEPT_WIZARD)
    assert "/^https?:\\/\\//i" in source
    assert "需以 http:// 或 https:// 开头" in source
    assert "canSubmit" in source


def test_accept_wizard_warns_when_credentials_missing():
    source = _read(ACCEPT_WIZARD)
    assert "未携带认证凭证" in source


def test_share_components_support_dark_theme():
    for path in (PERSONAL_MENU, SHARE_DIALOG, RECEIVED_PANEL, SENT_PANEL, ACCEPT_WIZARD):
        source = _read(path)
        assert "dark:" in source, f"{path.name} 缺少暗黑模式样式"
        assert "dark:text-" in source, f"{path.name} 缺少暗黑模式文字颜色"
        assert "dark:bg-" in source or "dark:border-" in source, f"{path.name} 缺少暗黑模式背景/边框"


def test_mcp_share_utils_delegate_status_meta():
    source = _read(FRONTEND / "utils" / "mcpShare.ts")
    assert "McpPersonalMenuKey" in source
    assert "McpShareItem" in source
    assert "shareStatusMeta" in source
