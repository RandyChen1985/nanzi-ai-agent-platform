"""契约：工具分组头必须能看出「整组已选」。

截图反馈：MCP 分组 `(8/8) 取消全选` 和 `(0/21) 一键全选` 的头部长得一模一样——
同样的淡 indigo 底、同样的浅色小字，46 个工具分四组时只能逐个读右侧计数，
分组折叠后连这行小字都看不见。现在按选中态给头部换底色、加左侧色条、
把计数换成实心徽章；未选中保留透明色条占位，切换时不跳布局。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

DRAWER = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()

MCP_GROUPS = DRAWER.split(
    'v-for="(tools, serverName) in currentScopeGroupedMcpTools"', 1
)[1]
STATIC_GROUPS = DRAWER.split('v-for="group in filteredGroupedTools"', 1)[1].split(
    "currentScopeGroupedMcpTools", 1
)[0]


def test_group_state_helper_has_three_states():
    body = DRAWER.split("const groupSelectionState", 1)[1].split("const mcpGroupState", 1)[0]

    assert "if (total <= 0) return 'none'" in body
    assert "if (selected >= total) return 'all'" in body
    assert "return selected > 0 ? 'some' : 'none'" in body


def test_mcp_group_header_highlights_fully_selected():
    assert "mcpGroupState(tools) === 'all'" in MCP_GROUPS
    assert "border-l-indigo-500" in MCP_GROUPS
    assert "border-b-indigo-200" in MCP_GROUPS and "bg-indigo-100" in MCP_GROUPS
    # 实心徽章：一眼能从一排分组里挑出「这组已经全选」
    assert "bg-indigo-600 font-bold text-white" in MCP_GROUPS


def test_partial_selection_is_distinguishable_from_none():
    assert "mcpGroupState(tools) === 'some'" in MCP_GROUPS
    assert "border-l-indigo-300" in MCP_GROUPS
    # 未选中用透明色条占位，避免切换选中时整行左右跳动
    assert "border-l-transparent" in MCP_GROUPS


def test_static_groups_use_same_visual_language():
    assert "staticGroupState(group.tools) === 'all'" in STATIC_GROUPS
    assert "border-l-primary" in STATIC_GROUPS
    assert "bg-primary font-bold text-white" in STATIC_GROUPS


def test_highlight_uses_same_count_as_bulk_toggle():
    """高亮和「一键全选/取消全选」必须同源，否则会出现「显示 8/8 却写着一键全选」。"""
    assert "props.getMcpGroupSelectedCount(list)" in DRAWER
    assert "props.getStaticGroupSelectedCount(list)" in DRAWER
    assert "isAllMcpSelected(serverName, tools) ? '取消全选' : '一键全选'" in DRAWER
