"""`/dashboard/mcp` 前端 Review 修复项契约测试（Batch 1 P0 + Batch 2 P1）。"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = ROOT / "frontend/src/views/McpManagement.vue"
REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"
TESTER = ROOT / "frontend/src/components/system/McpToolTester.vue"
DISPLAY_NAME = ROOT / "frontend/src/utils/mcpToolDisplayName.ts"
CRED_PANEL = ROOT / "frontend/src/components/mcp/PlatformMcpCredentialPanel.vue"
SHARE_DIALOG = ROOT / "frontend/src/components/mcp/McpShareDialog.vue"
CHANGES_DRAWER = ROOT / "frontend/src/components/mcp/McpDefinitionChangesDrawer.vue"
CHANGE_DETAIL = ROOT / "frontend/src/components/mcp/McpDefinitionChangeDetail.vue"
LOG_MODAL = ROOT / "frontend/src/components/system/McpServerLogModal.vue"
AUDIT_TAB = ROOT / "frontend/src/components/mcp/McpAuditLogTab.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_personal_only_non_admin_keeps_secondary_tabs_and_guide_switches_scope():
    src = _read(MANAGEMENT)
    # 个人中心 / 嵌入弹窗非管理员保留二级菜单
    assert 'v-if="!isAdmin && activeScope === \'personal\'"' in src
    # 指引横幅与帮助弹窗统一走 openAddFromGuide，从只读平台页/非 mine 视图自动切回个人 mine
    assert "const openAddFromGuide = async" in src
    assert "(!isAdmin.value && activeScope.value === 'global')" in src
    assert "openAddFromGuide('manual')" in src


def test_registry_credential_binding_and_permission_guards():
    src = _read(REGISTRY)
    # 1) 保留真实 is_global，解绑按实际层提交且识别 removed === 0
    assert "is_global?: boolean" in src
    assert "is_global: Boolean(row.is_global)" in src
    assert "headerBindings.value[slot]?.is_global" in src
    assert "removed === 0" in src
    # 2) 新建使用凭据中心绑定后自动补同步
    assert "if (boundCount > 0 && payload.enabled_status !== 0)" in src
    assert "/api/portal/mcp/servers/${newId}/sync" in src
    # 3) 切换鉴权/关开关/删行清理 pendingBindings，且保存中禁用取消按钮
    assert "clearPendingAuthBinding()" in src
    assert "pendingSnapshot" in src
    assert ':disabled="savingServer"' in src
    # 4) 只读态守卫 + 无 Token 不伪装只读占位符 + 独立批量锁与 togglePublish 竞态防护
    assert "if (!canSave.value)" in src
    assert "if (canSave.value && canSyncMcp.value && selectedServer.value?.id)" in src
    assert "isEditing && hasSavedAuthorizationToken && authorizationEditing" in src
    assert "const batchUpdating = ref(false)" in src
    assert "if (selectedServer.value?.id === targetServerId) selectedServerUsage.value = usage" in src
    # 5) 平台凭据按 owner_scope 动态提示 + 分组点击防锁死 watcher
    assert "scope === 'global' && mcpBindingPicked?.owner_scope === 'system'" in src
    assert "watch(mcpGroupFilter, (next) => {" in src
    assert '@click.stop="mcpGroupFilter = mcpGroupName(server)"' in src


def test_tool_tester_fixes_tab_collision_anyof_boolean_and_error_json():
    src = _read(TESTER)
    # 三个 Tab 必须互斥 v-if / v-else-if / v-else-if，避免 curl 页串渲染调用详情
    assert '<div v-if="activeTab === \'curl\'"' in src
    assert '<div v-else-if="activeTab === \'input\'"' in src
    assert '<div v-else-if="activeTab === \'details\'"' in src
    # Pydantic v2 anyOf/oneOf 解包与必填 boolean 默认 false
    assert "const unwrapSchemaProp = (prop: any)" in src
    assert "Array.isArray(prop.anyOf)" in src
    assert "getScalarType(prop) === 'boolean' && requiredFields.value.includes(key)" in src
    # 请求序号防切工具竞态 + 错误态不套用暗底极浅色 JSON 高亮
    assert "let executeSeq = 0" in src
    assert "let curlSeq = 0" in src
    assert 'v-if="!error && formattedResult.kind === \'json\' && formattedResult.html"' in src


def test_tool_display_name_requires_delimiter_boundary():
    src = _read(DISPLAY_NAME)
    assert "if (/^[:/._-]+/.test(tail))" in src


def test_platform_credential_panel_fixes_pagination_scene_slot_and_check():
    src = _read(CRED_PANEL)
    assert "page.value = 1" in src
    assert ":scene=\"editing.kind === 'mcp' ? 'mcp' : 'api_tool'\"" in src
    assert "function singleSlotOf(target: McpTarget)" in src
    assert "openBindMcp(item, singleSlotOf(item))" in src
    assert "canCheckSavedBinding" in src
    assert "editNeedsField.value = false" in src


def test_share_dialog_empty_state_reachable_and_search_seq_guarded():
    src = _read(SHARE_DIALOG)
    assert "const hasSearched = ref(false)" in src
    assert "v-if=\"searching || results.length || searchError || (hasSearched && keyword.trim())\"" in src
    assert "searchSeq += 1" in src


def test_definition_changes_drawer_uses_backend_counts_and_clean_readonly_copy():
    src = _read(CHANGES_DRAWER)
    assert "const backendCounts = res.data?.counts" in src
    assert 'v-if="item.status === \'pending\' && canManage"' in src
    assert 'v-else-if="item.status !== \'pending\'"' in src
    assert "status === 'unreachable'" in src
    assert "applied || ids.length" not in src
    assert "dismissed || ids.length" not in src
    # 详情渲染与后端 _param_snapshot 新增嵌套/约束字段闭环
    detail_src = _read(CHANGE_DETAIL)
    for key in (
        "allOf", "$ref", "properties", "nested_required",
        "additionalProperties", "minLength", "maxLength", "pattern",
    ):
        assert f"key: '{key}'" in detail_src, f"McpDefinitionChangeDetail.vue 缺少字段 {key}"


def test_audit_log_views_support_all_range_and_falsy_json_and_copy_result():
    for path in (LOG_MODAL, AUDIT_TAB):
        src = _read(path)
        assert "val === null || val === undefined || val === ''" in src
        assert "const ok = await copyToClipboard(textToCopy)" in src
        assert "let logsRequestSeq = 0" in src
        assert "Number.isNaN(d.getTime())" in src
    modal_src = _read(LOG_MODAL)
    assert "range: timeRangeFilter.value" in modal_src
    assert "params.range = '30d'" not in modal_src
