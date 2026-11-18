"""工具能力步骤：搜索框右侧的「仅看已选」快速过滤（用户需求）。

背景：一个版本动辄勾 36/172 个工具，事后想核对「我到底选了哪几个」只能靠搜索框
一个个搜。用户要求在搜索框里加一个开关，只显示已勾选的工具 / MCP（截图红框位置）。

设计：
1. 开关跨三个页签共用（系统工具 / MCP 工具 / Skills），放在搜索框内右侧，激活时高亮；
2. 与搜索关键词是**与**关系（既已选又命中关键词）；
3. 过滤态下分组一律展开（与搜索同口径，只读覆盖、不写回折叠集合），否则收窄后的
   分组还是折叠着，等于什么都看不到；
4. 分组头的 x/y 分子分母取**全量**分组：过滤视图里若都取视图，`(2/2)` 会让人误以为
   这组本来只有 2 个工具；
5. 过滤后为空要区分「没勾任何东西」和「关键词没命中」，前者提示关掉过滤；
6. 开关是内存态，重置抽屉时复位。

契约测试读源码断言（前端无 DOM 环境），用花括号配平取完整函数体，避免固定窗口
在格式化后假红/假绿。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = ROOT / "frontend/src/views/AgentManagement.vue"
DRAWER = ROOT / "frontend/src/components/agent/AgentVersionEditorDrawer.vue"


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


def test_management_declares_the_toggle_state():
    source = _source(MANAGEMENT)
    assert re.search(r"const onlySelectedTools = ref\(false\)", source)


@pytest.mark.parametrize(
    "computed_name,selector",
    [
        ("filteredGroupedTools", "isToolSelected"),
        ("filteredGroupedMcpTools", "isToolSelected"),
        ("filteredEnabledSkills", "isSkillSelected"),
    ],
)
def test_filters_gate_on_selection(computed_name: str, selector: str):
    """三个页签都要真的按勾选状态过滤，漏一个就会出现「切过去还是全量」的不一致。"""
    body = _body(_source(MANAGEMENT), computed_name)
    assert "onlySelectedTools.value" in body
    assert selector in body


def test_mcp_filter_no_longer_short_circuits_on_empty_query():
    """MCP 过滤原先有 `if (!q) return groups` 的早退，只加搜索条件会绕过勾选过滤。"""
    body = _body(_source(MANAGEMENT), "filteredGroupedMcpTools")
    assert "if (!q && !onlySelected) return groups" in body


def test_drawer_and_management_wire_the_toggle():
    drawer = _source(DRAWER)
    management = _source(MANAGEMENT)

    assert re.search(r"onlySelectedTools\?: boolean;", drawer)
    assert re.search(r"'update:onlySelectedTools': \[value: boolean\];", drawer)
    assert ':only-selected-tools="onlySelectedTools"' in management
    assert '@update:only-selected-tools="onlySelectedTools = $event"' in management


def test_toggle_is_a_two_segment_text_switch_beside_the_search_box():
    """控件是「已选 / 全部」文字两段式，紧跟在搜索框之后。

    最初做的是搜索框内的漏斗图标，用户反馈「文字比图标更显眼」——图标要靠 hover 才
    知道是漏斗，而「已选 / 全部」一眼就知道当前在看什么，所以改成两段式并挪到框外。
    """
    drawer = _source(DRAWER)
    input_index = drawer.index('placeholder="搜索工具名称或描述..."')
    switch_index = drawer.index('aria-label="按勾选状态过滤工具范围"')
    assert input_index < switch_index

    # 从 aria-label 往前多看一点：role="group" 写在它前面，容器标签也在那儿
    block = drawer[max(0, switch_index - 200) : switch_index + 1700]
    assert 'role="group"' in block
    assert ">已选</button>" in block
    assert ">全部</button>" in block
    # 两个按钮各自反映当前状态（可访问性：读屏能报出哪一个被按下）
    assert ':aria-pressed="onlySelectedTools"' in block
    assert ':aria-pressed="!onlySelectedTools"' in block
    # 搜索框右内边距回到常规值：不再给内嵌图标留位置
    assert "pl-8 pr-3 py-2" in drawer


def test_filtering_state_covers_both_filters():
    """分组展开的判据必须同时覆盖搜索与「仅看已选」。"""
    drawer = _source(DRAWER)
    assert re.search(
        r"const isFiltering = computed\(\(\) => isSearching\.value \|\| props\.onlySelectedTools\)",
        drawer,
    )
    template = drawer.split("<template>", 1)[1]
    # 模板里只允许通过 isFiltering 判断，出现裸 isSearching 说明有地方漏改
    assert "isSearching" not in template


def test_group_counters_use_full_groups():
    """分组头 x/y 取全量分组，过滤视图下分母不跟着缩小。"""
    source = _source(MANAGEMENT)
    static_body = _body(source, "staticGroupCounts")
    mcp_body = _body(source, "mcpGroupCounts")

    assert "getStaticGroupTools" in static_body
    assert "filteredGroupedTools" not in static_body
    assert "groupedMcpTools" in mcp_body
    assert "filteredGroupedMcpTools" not in mcp_body


def test_empty_state_distinguishes_the_two_filters():
    drawer = _source(DRAWER)
    body = _body(drawer, "filterEmptyHint")
    assert "isSearching.value && props.onlySelectedTools" in body
    assert "还没有勾选任何" in body
    # 三个页签列表都要用同一个提示函数
    assert drawer.count("filterEmptyHint(") >= 3


def test_toggle_resets_with_the_editor():
    """三个入口（重置抽屉 / 新建版本 / 未完成配置继续）都要把开关收回「显示全部」。"""
    source = _source(MANAGEMENT)
    assert _body(source, "resetVersionEditorUi").count("onlySelectedTools.value = false") == 1
    assert source.count("onlySelectedTools.value = false") >= 3
