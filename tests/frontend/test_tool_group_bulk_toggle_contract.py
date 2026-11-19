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
4. 折叠状态仍是内存态（重置抽屉由 ``resetVersionEditorUi`` 复位），不引入持久化；
5. 两个页签的分组**默认折叠**：状态一律记「已展开」（空集 = 全收起），进来先看
   分组标题与各组 x/y，想改哪组再点开——系统工具与 MCP 工具同口径。

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


def test_expand_all_fills_every_group():
    """两个页签的分组默认都折叠（展开集合默认空），「全部展开」得把全量分组填进去。

    改用「记录已展开」之后，一键展开与一键折叠的职责互换了：填满 vs 清空。
    静态工具先改，MCP 页签随后对齐——别再一边记折叠、一边记展开，否则两个页签
    的默认状态会再次分叉（用户反馈的正是「MCP 一进来全展开、系统工具是折叠的」）。
    """
    source = _source(MANAGEMENT)
    static_expand = _body(source, "expandAllStaticGroups")
    mcp_expand = _body(source, "expandAllMcpGroups")

    assert "groupedTools" in static_expand
    assert "filteredGroupedTools" not in static_expand
    # MCP 侧必须用全量分组而不是搜索过滤后的视图，否则批量操作会被悄悄缩小范围
    assert "groupedMcpTools" in mcp_expand
    assert "filteredGroupedMcpTools" not in mcp_expand
    assert "expandedMcpGroups.value = new Set" in mcp_expand
    assert "filter" not in _line_with(mcp_expand, "expandedMcpGroups.value =")


def test_collapse_all_clears_the_expanded_set():
    """「全部折叠」= 清空展开集合，两个页签同口径。"""
    source = _source(MANAGEMENT)
    static_body = _body(source, "collapseAllStaticGroups")
    mcp_body = _body(source, "collapseAllMcpGroups")

    assert re.search(r"expandedStaticGroups\.value = new Set(?:<string>)?\(\)", static_body)
    assert re.search(r"expandedMcpGroups\.value = new Set(?:<string>)?\(\)", mcp_body)
    # 清空集合不该被搜索框或子 scope 影响，也不该退化成按过滤结果逐组删除
    assert "filteredGroupedMcpTools" not in mcp_body
    assert "filter" not in _line_with(mcp_body, "expandedMcpGroups.value =")


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
    """两种过滤态（搜索 /「已选」）都强制展开分组。

    判据后来合并成 ``isFiltering``：它 = isSearching || onlySelectedTools。
    两者都会把分组收窄到只剩几条，折叠着等于看不到东西。
    """
    source = _source(DRAWER)
    assert "const isSearching = computed" in source
    assert re.search(
        r"const isFiltering = computed\(\(\) => isSearching\.value \|\| props\.onlySelectedTools\)",
        source,
    )
    assert re.search(r"!isStaticGroupCollapsed\(group\.label\)\s*\|\|\s*isFiltering", source)
    assert re.search(r"!isMcpGroupCollapsed\(serverName\)\s*\|\|\s*isFiltering", source)


def test_group_header_toggle_is_disabled_while_searching():
    """搜索期分组强制展开；若标题按钮仍可点，会出现「点了没反应却改了状态」的延迟生效。"""
    source = _source(DRAWER)
    for needle in (
        "emit('toggleStaticGroupCollapse', group.label)",
        "emit('toggleMcpGroupCollapse', serverName)",
    ):
        tag = _element_tag(source, needle)
        assert ':disabled="isFiltering"' in tag, tag


@pytest.mark.parametrize("name", BULK_HANDLERS)
def test_bulk_toggle_does_not_clear_the_search_box(name: str):
    assert "toolSearchQuery" not in _body(_source(MANAGEMENT), name)


def test_bulk_toggle_stays_in_memory():
    source = _source(MANAGEMENT)
    window = _body(source, "collapseAllStaticGroups") + _body(source, "collapseAllMcpGroups")
    assert "localStorage" not in window
    assert "sessionStorage" not in window


def test_tool_groups_are_collapsed_by_default():
    """进「工具能力」先看到一屏分组标题（各组 x/y 一目了然），想改哪组再点开。

    以前状态记的是「已折叠」、默认空集 = 全部展开，一进来就是几十张工具卡片。
    静态工具先改成「记录已展开」；MCP 页签此前仍是旧语义，进 MCP 就是全展开
    （用户反馈），现在两个页签统一——判折叠一律走 ``!expanded*.has(...)``。
    """
    source = _source(MANAGEMENT)
    assert "const expandedStaticGroups = ref<Set<string>>(new Set())" in source
    assert (
        "const isStaticGroupCollapsed = (label: string) => !expandedStaticGroups.value.has(label)"
        in source
    )
    assert "const expandedMcpGroups = ref<Set<string>>(new Set())" in source
    assert (
        "const isMcpGroupCollapsed = (serverName: string) => !expandedMcpGroups.value.has(serverName)"
        in source
    )
    # 反方向语义不能再回来：空集 = 全展开
    assert "collapsedMcpGroups" not in source


def test_reset_still_clears_group_collapse_state():
    """重开版本配置要回到「全部分组收起」：两个展开集合都必须清空。"""
    body = _body(_source(MANAGEMENT), "resetVersionEditorUi")
    assert re.search(r"expandedMcpGroups\.value = new Set(?:<string>)?\(\)", body)
    assert re.search(r"expandedStaticGroups\.value = new Set(?:<string>)?\(\)", body)
