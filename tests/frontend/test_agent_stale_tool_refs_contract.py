"""编辑器内的「失效工具引用」解绑入口（前端契约）。

用户场景：删除 MCP 服务后，智能体版本里仍保留 `server:tool` 引用，但工具选择器
只渲染可用工具，旧引用既看不见也取消不了。这组测试锁住三个关键点：
1. 编辑器能拿到后端诊断并按版本展示；
2. 移除只改本地表单，不直接调删除/更新接口；
3. 「全部移除」「单项移除」「重新检测」三个入口存在。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

API = Path("frontend/src/api/agent.ts").read_text()
MANAGEMENT = Path("frontend/src/views/AgentManagement.vue").read_text()
DRAWER = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()


def test_agent_api_exposes_version_tool_health():
    assert "getVersionToolHealth" in API
    assert "/versions/${versionId}/tool-health" in API
    assert "StaleToolRef" in API
    assert "stale_tools" in API


def test_management_fetches_health_when_opening_editor():
    assert "agentApi.getVersionToolHealth" in MANAGEMENT
    open_modal = MANAGEMENT.split("const openVersionModal", 1)[1].split(
        "const handleDrawerEditVersion", 1
    )[0]
    assert "fetchVersionToolHealth()" in open_modal


def test_management_checks_unsaved_versions_through_stateless_endpoint():
    """新建/克隆版本还没有 id：不能直接跳过，否则克隆来的幽灵引用永远不提示。"""
    fetch_body = MANAGEMENT.split("const fetchVersionToolHealth", 1)[1].split(
        "const removeStaleToolRefs", 1
    )[0]

    assert "const versionId = versionForm.value?.id" in fetch_body
    # 只有「没有版本 id 且没有任何工具」才跳过；有工具就必须诊断
    assert "(!versionId && tools.length === 0)" in fetch_body
    assert "agentApi.getVersionToolHealth(agentId, versionId)" in fetch_body
    assert "agentApi.checkToolsHealth(agentId, tools)" in fetch_body
    # 连续切版本/重复点检测时丢弃过期响应
    assert "versionToolHealthRequestSeq" in fetch_body


def test_removing_stale_refs_only_mutates_local_form():
    remove_body = MANAGEMENT.split("const removeStaleToolRefs", 1)[1].split(
        "watch(versionConfigStep", 1
    )[0]
    assert "versionForm.value.tools" in remove_body
    # 只做本地过滤：任何直接写库/删工具的动作都会绕过「保存版本」这一确认步骤
    assert "agentApi." not in remove_body
    assert "axios" not in remove_body
    assert "保存版本后生效" in remove_body
    # tools 可能是字符串或带配置的对象，两种都要能移除
    assert "(item as any)?.name" in remove_body


def test_drawer_renders_stale_refs_card_and_badge():
    assert "项已失效的工具引用" in DRAWER
    assert "staleToolRefs" in DRAWER
    assert "staleToolRefsLoading" in DRAWER
    assert "staleToolRefsError" in DRAWER
    assert "项失效" in DRAWER
    assert "reason_label" in DRAWER


def test_drawer_offers_remove_all_remove_one_and_recheck():
    assert "emit('removeStaleToolRefs', staleToolRefs.map((item) => item.name))" in DRAWER
    assert "emit('removeStaleToolRefs', [item.name])" in DRAWER
    assert "emit('refreshStaleToolRefs')" in DRAWER
    # 只读版本不能移除
    card = DRAWER.split("项已失效的工具引用", 1)[0]
    assert 'v-if="canEditVersion"' in DRAWER
    assert "全部移除" in DRAWER


def test_drawer_reports_check_failure_without_blocking_editing():
    assert "失效引用检测失败" in MANAGEMENT
    assert "重试" in DRAWER


def test_readonly_version_explains_how_to_apply_removal():
    """已发布版本不可编辑：卡片必须说明「改草稿再发布」，否则用户以为没有解绑入口。"""
    assert "当前版本不可编辑（已发布或只读）" in DRAWER
    assert "编辑草稿版本、移除后重新发布" in DRAWER


REGISTRY = Path("frontend/src/components/system/McpServerRegistry.vue").read_text()


def test_delete_toast_reports_auto_pruned_agent_versions():
    assert "affected_agent_versions" in REGISTRY
    assert "个智能体版本中移除失效引用" in REGISTRY
    # 删除前就告诉用户会自动清理，避免他再去智能体里找那个已经消失的 MCP
    assert "无需再去智能体里手动解绑" in REGISTRY


def test_stale_refs_card_sits_outside_the_tool_tabs():
    """失效引用卡片必须在页签内容之外。

    这些引用跨系统工具 / MCP / Skills 三类，此前卡片的模板挂在 `toolTab === 'mcp'`
    分支内部，于是站在「系统工具」页签（或 Skills）时完全看不到它——而它又是
    用户唯一的解绑入口，看不到就等于「无解」。
    """
    card_at = DRAWER.index("项已失效的工具引用")
    static_tab_at = DRAWER.index('v-if="toolTab === \'static\'"')
    mcp_tab_at = DRAWER.index('v-else-if="toolTab === \'mcp\'"')
    skills_tab_at = DRAWER.index('v-else class="flex-1 overflow-y-auto space-y-3 pr-1 version-editor-scroll"')

    assert card_at < static_tab_at, "失效引用卡片不应出现在系统工具页签之后"
    assert card_at < mcp_tab_at, "失效引用卡片不应挂在 MCP 页签内部"
    assert static_tab_at < mcp_tab_at < skills_tab_at
