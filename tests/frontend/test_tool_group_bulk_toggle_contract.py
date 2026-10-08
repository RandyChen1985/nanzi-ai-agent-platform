"""工具能力步骤的工具分组：一键展开 / 一键折叠 + 搜索命中时自动展开。

背景（用户反馈）：工具能力步骤里分组很多（系统工具按能力域分组、MCP 工具按服务名
分组），想看全貌或全收起时只能一个个点分组标题，希望有一键操作。

用户确认的设计：
1. 两个按钮（全部展开 / 全部折叠）放在工具栏、页签分段控件之前，只作用于**当前页签**；
   Skills tab 没有分组，不显示这两个按钮；
2. 作用域是该 tab 的**全部分组**——不受搜索框过滤影响，也不区分 MCP 的
   「平台 MCP / 我的 MCP」子 scope。否则搜索时点「全部折叠」会被悄悄缩小作用范围，
   清空搜索后还剩一堆分组是展开的；
3. 搜索关键词非空时，匹配到的分组一律展开（只读覆盖，不写回折叠集合），清空搜索即
   回到用户原本的折叠状态；否则用户搜到了工具，却只能看到折叠的分组标题。搜索期
   分组标题的折叠交互会被展示层覆盖，因此必须禁用，避免「点了没反应却改了状态」；
4. 折叠状态仍是内存态（重置抽屉由 ``resetVersionEditorUi`` 复位），不引入持久化。

断言取体用花括号配平、判定用正则（容忍换行与空格），不用固定字符窗口——否则模板
格式化或前文加一行注释就会假红，而写错的守卫反而可能假绿。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = ROOT / "frontend/src/views/AgentManagement.vue"
DRAWER = ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue"

#: 「全部展开 / 全部折叠」在父组件里的四个处理器 ↔ 抽屉绑定的 kebab-case 事件名
BULK_HANDLERS = (
    "expandAllStaticGroups",
    "collapseAllStaticGroups",
    "expandAllMcpGroups",
    "collapseAllMcpGroups",
)
BULK_BINDINGS = (
    ("expandAllStaticGroups", "expand-all-static-groups"),
    ("collapseAllStaticGroups", "collapse-all-static-groups"),
    ("expandAllMcpGroups", "expand-all-mcp-groups"),
    ("collapseAllMcpGroups", "collapse-all-mcp-groups"),
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _balanced_body(source: str, marker: str) -> str:
    """从 ``marker`` 起按花括号配平取完整片段（不做长度截断）。"""
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
    """取 ``const <name> = ...`` 的完整定义。"""
    return _balanced_body(source, f"const {name} = ")


def _line_with(source: str, needle: str) -> str:
    for line in source.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"源码里找不到包含 {needle!r} 的行")


def _element_tag(source: str, needle: str) -> str:
    """取包含 ``needle`` 的那个开始标签（从最近的 ``<`` 到其后的第一个 ``>``）。"""
    index = source.index(needle)
    start = source.rindex("<", 0, index)
    end = source.index(">", index)
    return source[start : end + 1]


def _bulk_button_container_tag(source: str, anchor: str) -> str:
    """取包含 ``anchor`` 的批量按钮所在容器的开始标签（用于断言守卫条件）。"""
    index = source.index(anchor)
    start = source.rindex("<div", 0, index)
    end = source.index(">", start)
    return source[start : end + 1]


@pytest.mark.parametrize("name", BULK_HANDLERS)
def test_bulk_handlers_exist_in_management(name: str):
    assert f"const {name} = " in _source(MANAGEMENT)


@pytest.mark.parametrize("name", BULK_HANDLERS)
def test_drawer_declares_bulk_emit(name: str):
    source = _source(DRAWER)
    assert re.search(rf"\b{name}: \[\];", source), f"抽屉没有声明 {name} 事件"


@pytest.mark.parametrize("handler,kebab", BULK_BINDINGS)
def test_management_binds_bulk_handlers(handler: str, kebab: str):
    """Vue 对未声明的监听器不报错：绑定被删掉后按钮只是静默失效，必须守着。"""
    assert f'@{kebab}="{handler}"' in _source(MANAGEMENT)


def test_expand_all_clears_the_collapsed_set():
    source = _source(MANAGEMENT)
    assert re.search(
        r"collapsedStaticGroups\.value = new Set(?:<string>)?\(\)",
        _body(source, "expandAllStaticGroups"),
    )
    assert re.search(
        r"collapsedMcpGroups\.value = new Set(?:<string>)?\(\)",
        _body(source, "expandAllMcpGroups"),
    )


def test_collapse_all_targets_every_group_not_the_filtered_view():
    """「全部折叠」必须用全量分组 key；用 filtered* 会被搜索框或子 scope 缩小作用域。"""
    source = _source(MANAGEMENT)
    static_body = _body(source, "collapseAllStaticGroups")
    mcp_body = _body(source, "collapseAllMcpGroups")

    assert "filteredGroupedTools" not in static_body
    assert "groupedTools" in static_body
    assert "filteredGroupedMcpTools" not in mcp_body
    assert "groupedMcpTools" in mcp_body

    # 更强的一层：真正写入折叠集合的那一行也不得出现 filter
    assert "filter" not in _line_with(static_body, "collapsedStaticGroups.value =")
    assert "filter" not in _line_with(mcp_body, "collapsedMcpGroups.value =")


def test_toolbar_renders_both_buttons_before_the_tabs():
    source = _source(DRAWER)
    expand_button = source.index('@click="emitBulkExpandGroups"')
    collapse_button = source.index('@click="emitBulkCollapseGroups"')
    assert min(expand_button, collapse_button) < source.index(">系统工具 (")


def test_bulk_buttons_are_hidden_on_the_skills_tab():
    """Skills tab 没有分组，按钮不该出现在那里。"""
    tag = _bulk_button_container_tag(_source(DRAWER), '@click="emitBulkExpandGroups"')
    guard = re.search(r'v-if="([^"]*)"', tag)
    assert guard, f"批量按钮容器缺少 v-if 守卫: {tag}"
    normalized = guard.group(1).replace(" ", "")
    assert normalized in {"toolTab!=='skills'", "toolTab==='static'||toolTab==='mcp'"}, normalized


def test_bulk_buttons_expose_accessible_names_and_focus_ring():
    """键盘与读屏用户必须能知道按钮作用域，并能看到焦点位置。

    锚点取行为（emit 绑定）而非按钮文案：这两个按钮是纯图标按钮，改文案或换图标
    属于视觉调整，不应该让契约假红；但 aria-label 与焦点环必须一直在。
    """
    source = _source(DRAWER)
    for anchor in ('@click="emitBulkExpandGroups"', '@click="emitBulkCollapseGroups"'):
        tag = _element_tag(source, anchor)
        assert "aria-label=" in tag, tag
        assert "focus-visible:ring" in tag, tag


def test_collapsed_groups_expand_while_searching():
    source = _source(DRAWER)
    assert "const isSearching = computed" in source
    assert re.search(r"!isStaticGroupCollapsed\(group\.label\)\s*\|\|\s*isSearching", source)
    assert re.search(r"!isMcpGroupCollapsed\(serverName\)\s*\|\|\s*isSearching", source)


def test_group_header_toggle_is_disabled_while_searching():
    """搜索期分组强制展开；若标题按钮仍可点，会出现「点了没反应却改了状态」的延迟生效。"""
    source = _source(DRAWER)
    for needle in (
        "emit('toggleStaticGroupCollapse', group.label)",
        "emit('toggleMcpGroupCollapse', serverName)",
    ):
        tag = _element_tag(source, needle)
        assert ':disabled="isSearching"' in tag, tag


@pytest.mark.parametrize("name", BULK_HANDLERS)
def test_bulk_toggle_does_not_clear_the_search_box(name: str):
    assert "toolSearchQuery" not in _body(_source(MANAGEMENT), name)


def test_bulk_toggle_stays_in_memory():
    source = _source(MANAGEMENT)
    window = _body(source, "collapseAllStaticGroups") + _body(source, "collapseAllMcpGroups")
    assert "localStorage" not in window
    assert "sessionStorage" not in window


def test_reset_still_clears_group_collapse_state():
    body = _body(_source(MANAGEMENT), "resetVersionEditorUi")
    assert re.search(r"collapsedMcpGroups\.value = new Set(?:<string>)?\(\)", body)
    assert re.search(r"collapsedStaticGroups\.value = getDefaultCollapsedStaticGroups\(\)", body)
