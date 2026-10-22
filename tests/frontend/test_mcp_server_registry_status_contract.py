from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

SOURCE = Path(
    "frontend/src/components/system/McpServerRegistry.vue"
).read_text()


def test_mcp_server_card_exposes_enabled_status_switch():
    assert "import Switch from '../Switch.vue'" in SOURCE
    assert "toggleServerStatus" in SOURCE
    assert "server.enabled_status === 1" in SOURCE
    assert "已禁用" in SOURCE


def test_mcp_server_status_switch_updates_existing_server_configuration():
    assert "@update:model-value=\"handleServerStatusChange(server, $event)\"" in SOURCE
    assert "const handleServerStatusChange = async" in SOURCE
    assert ':loading="statusLoading[server.id]"' in SOURCE
    assert "`/api/portal/mcp/servers/${server.id}`" in SOURCE
    assert "enabled_status: nextStatus" in SOURCE


def test_disabled_server_blocks_tool_actions_and_shows_effective_status():
    assert "const isSelectedServerEnabled = computed" in SOURCE
    assert "服务已禁用" in SOURCE
    assert "canManageSelectedTools" in SOURCE
    assert ":disabled=\"!canManageSelectedTools\"" in SOURCE


def test_remote_deleted_tool_is_visible_but_cannot_be_managed():
    assert "remote_deleted_count" in SOURCE
    assert "远端已删除" in SOURCE
    assert "tool?.is_available !== false" in SOURCE
    assert ":disabled=\"!canManageTool(tool)\"" in SOURCE


def test_remote_deleted_hint_explains_itself_via_platform_tooltip():
    """「远端已删除」是存量提示不是故障，必须解释原因/影响/恢复路径。

    用全局 v-tooltip 指令（挂 body、可控换行、移动端可点），不能用原生 title：
    原生 title 约 1 秒才出现、长文案被压成一行，等于没解释。
    """
    assert "个远端已删除" in SOURCE
    assert "v-tooltip=" in SOURCE
    # 触发区要给出「可悬停」的视觉暗示
    assert "cursor-help" in SOURCE
    # 文案必须覆盖「为什么出现」与「怎么恢复」两件事
    assert "常见原因是远端改名、下架" in SOURCE
    assert "远端恢复同名工具后" in SOURCE


def test_remote_deleted_tools_have_a_guarded_cleanup_action():
    """长期无法自愈时必须有出口：清理按钮 + 二次确认 + 只打 stale-tools 接口。

    清理走 DELETE /servers/{id}/stale-tools，而不是删服务——删服务会因
    sys_mcp_outbound_audit_logs 的 ondelete=CASCADE 连带清空出站审计日志。
    """
    assert "stale-tools" in SOURCE
    assert "清理已失效" in SOURCE
    assert "const cleanupStaleTools = async" in SOURCE
    # 删除类动作要 delete 权限，且必须经过确认弹窗
    assert "canDeleteMcp && staleToolCount > 0" in SOURCE
    assert "showCleanupConfirm" in SOURCE
    assert "将被清理的工具" in SOURCE

    # 前端不能退化成「删服务」来清缓存：清理只打 stale-tools 子资源
    cleanup_block = SOURCE.split("const cleanupStaleTools = async", 1)[1].split(
        "const fetchTools", 1
    )[0]
    assert "/stale-tools`" in cleanup_block
    assert "executeDeleteServer" not in cleanup_block
