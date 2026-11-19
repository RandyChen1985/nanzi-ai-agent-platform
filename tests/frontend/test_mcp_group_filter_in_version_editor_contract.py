"""版本编辑器的 MCP 页签：按业务分组过滤（用户需求）。

背景：MCP 管理页（「我的 MCP」）已经有「全部 / 出行服务 / 未分组」的分组标签，但
版本配置的「工具能力 → MCP 工具」里没有，几十个服务几十个工具只能靠搜索。用户要求
在「平台 MCP / 我的 MCP」子页签下面加同一套分组标签。

设计要点（都是踩过坑的那几条）：
1. 分组是**服务级**属性（``McpServer.group_name``），同一服务的工具必然同组，所以过滤
   作用在「服务分组」这一层，不做工具级分组；
2. 空白 group_name 统一归到「未分组」，哨兵值必须与 MCP 管理页一致（``__ungrouped__``），
   否则两处口径分叉；
3. 分组选项与计数取**全量**分组，不跟着搜索收窄——否则用户在某个分组里搜关键词、该组
   命中变少到消失时，选中的分组会被悄悄清空；
4. 切「平台 / 我的」时分组集合完全不同，必须把选中的分组清掉，否则列表直接空；
5. 只有一个分组时不渲染这行 chips（没有过滤价值，只是噪音）。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRAWER = ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue"
MANAGEMENT = ROOT / "frontend/src/views/AgentManagement.vue"
REGISTRY = ROOT / "frontend/src/components/system/McpServerRegistry.vue"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _balanced_body(source: str, marker: str) -> str:
    start = source.index(marker)
    open_index = source.index("{", start)
    depth = 0
    for index in range(open_index, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"花括号未配平: {marker}")


def _body(source: str, name: str) -> str:
    return _balanced_body(source, f"const {name} = ")


def test_ungrouped_sentinel_matches_the_registry_page():
    """两处都用「未分组」这个概念，哨兵值必须一致，否则同一服务在两边归属不同。"""
    drawer = _source(DRAWER)
    registry = _source(REGISTRY)
    assert re.search(r"MCP_UNGROUPED_KEY = '__ungrouped__'", drawer)
    assert "__ungrouped__" in registry


def test_group_filter_is_server_level():
    """过滤按服务分组，不是按工具名：同一服务下所有工具一起进出。"""
    body = _body(_source(DRAWER), "visibleScopeGroupedMcpTools")
    assert "currentScopeGroupedMcpTools" in body
    assert "mcpGroupOfServer(tools)" in body
    assert "mcpGroupFilter" in body


def test_group_options_use_full_groups_not_filtered_view():
    """chips 的选项/计数来自全量分组，搜索不会把选中分组挤没。"""
    drawer = _source(DRAWER)
    options_body = _body(drawer, "mcpGroupOptions")
    assert "scopeGroupsOf(props.groupedMcpTools" in options_body
    assert "filteredGroupedMcpTools" not in options_body

    total_body = _body(drawer, "mcpScopeToolsTotal")
    assert "props.groupedMcpTools" in total_body


def test_drawer_receives_full_groups_and_management_passes_them():
    drawer = _source(DRAWER)
    management = _source(MANAGEMENT)
    assert re.search(r"groupedMcpTools\?: Record<string, any\[\]>;", drawer)
    assert re.search(r"groupedMcpTools: \(\) => \(\{\}\)", drawer)
    assert ':grouped-mcp-tools="groupedMcpTools"' in management


def test_scope_switch_clears_the_group_filter():
    drawer = _source(DRAWER)
    assert re.search(
        r"watch\(mcpSubTab, \(\) => \{\s*mcpGroupFilter\.value = '';\s*\}\)",
        drawer,
    )


def test_list_renders_the_grouped_view():
    """列表与空态都切到分组过滤后的视图，否则标签点了没反应。"""
    drawer = _source(DRAWER)
    template = drawer.split("<template>", 1)[1]
    assert 'v-for="(tools, serverName) in visibleScopeGroupedMcpTools"' in template
    assert "currentScopeGroupedMcpToolsCount" in template


def test_chips_only_render_when_there_is_more_than_one_group():
    drawer = _source(DRAWER)
    assert 'v-if="mcpGroupOptions.length > 1"' in drawer


def test_empty_group_state_has_its_own_hint():
    drawer = _source(DRAWER)
    assert "该分组下没有可用的 MCP 工具" in drawer


def test_grouping_field_comes_from_the_backend():
    """``group_name`` 由 ``/api/portal/tools/mcp`` 提供，前端只做透传。"""
    backend = (ROOT / "app/api/portal/endpoints/tools.py").read_text(encoding="utf-8")
    assert '"group_name"' in backend
