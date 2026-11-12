"""工具注册表「业务分组页签」契约：过滤口径、命中计数、页签顺序、两页同源。

需求来源（用户附截图 + 后续追问）：API 工具页与脚本工具页都要能按业务分组多 Tab 展示，
新建 / 编辑有业务分组、顶部能按它筛。方案见 `prototypes/2026-11-12-tool-group-tabs.html`
（方案 A：页签与筛选下拉共用 groupFilter）。

核心断言**真跑** `frontend/src/utils/toolGroupTabs.ts`（Node 探针加载真实模块），所以测的
就是页面渲染页签时用的那份计算，不是测试里另写一遍的近似实现。页签条本身抽成了
`ToolGroupTabs.vue`，两页共用——所以源码侧还要锁「两个页面都真的接上了」。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "frontend/src/components/system/ToolRegistry.vue"
SCRIPT_LIST = ROOT / "frontend/src/components/system/ScriptToolList.vue"
TABS_COMPONENT = ROOT / "frontend/src/components/system/ToolGroupTabs.vue"
UTIL = ROOT / "frontend/src/utils/toolGroupTabs.ts"
PROBE = ROOT / "tests/frontend/tool_group_tabs_probe.cjs"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _probe() -> dict:
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"探针执行失败：\n{proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# 真跑：页签数据（API 工具 12 个 / 脚本工具 4 个）
# --------------------------------------------------------------------------


def test_tabs_start_with_all_and_end_with_ungrouped() -> None:
    """「全部」打头、「未分组」殿后，中间各组按命中数降序——顺序固定才谈得上肌肉记忆。"""
    out = _probe()
    assert out["base_total"] == 12
    assert out["base_first_key"] == "all"
    assert out["base_last_key"] == "__ungrouped__"
    assert out["base_items"] == [
        ["全部", 12],
        ["履约罚款", 4],
        ["设备巡检", 3],
        ["工单处理", 2],
        ["未分组", 3],
    ]
    assert out["base_group_counts"] == sorted(out["base_group_counts"], reverse=True)


def test_method_filter_counts_hit_and_drops_empty_groups() -> None:
    """页签计数按「关键词 + 方法 + 状态」过滤后的命中算；没有命中的组不该出现。"""
    out = _probe()
    assert out["post_total"] == 3
    assert out["post_items"] == [
        ["全部", 3],
        ["工单处理", 1],
        ["履约罚款", 1],
        ["设备巡检", 1],
    ]
    assert out["post_has_empty_group"] is False


def test_status_and_keyword_filters_are_reflected_in_counts() -> None:
    out = _probe()
    assert out["inactive_total"] == 2
    assert out["inactive_items"] == [["全部", 2], ["履约罚款", 1], ["未分组", 1]]
    assert out["kw_total"] == 4
    assert out["kw_items"] == [["全部", 4], ["履约罚款", 4]]


def test_no_hit_leaves_only_the_all_tab() -> None:
    """三层条件叠加后什么都不命中时，只剩「全部 0」——不能留一堆 0 计数的页签。"""
    out = _probe()
    assert out["none_total"] == 0
    assert out["none_items"] == [["全部", 0]]


def test_ungrouped_tab_hidden_when_everything_is_grouped() -> None:
    out = _probe()
    assert out["grouped_labels"] == ["全部", "履约罚款", "设备巡检", "工单处理"]
    assert out["grouped_has_ungrouped"] is False


def test_blank_group_name_counts_as_ungrouped() -> None:
    """空串、纯空格、null 都算未分组：历史数据里三种都有。"""
    assert _probe()["blank_is_ungrouped"] == 3


def test_matches_tool_filters_contract() -> None:
    out = _probe()
    assert out["match_default"] is True
    assert out["match_inactive_only"] is True
    # 未知状态值不放行：宁可少显示，也不要把停用工具当成启用的显示出来
    assert out["match_unknown_status_keeps_active"] is False


def test_script_tools_use_the_same_tab_calculation() -> None:
    """脚本工具页用同一份计算，只是多一个「来源」维度。"""
    out = _probe()
    assert out["script_base_items"] == [["全部", 4], ["财务对账", 2], ["工单处理", 1], ["未分组", 1]]
    assert out["script_custom_items"] == [["全部", 1], ["财务对账", 1]]
    assert out["script_builtin_total"] == 3


def test_script_keyword_matches_display_name_only_when_declared() -> None:
    """脚本工具要能按中文名（display_name）搜到；不传 searchFields 时搜不到——证明参数生效。"""
    out = _probe()
    assert out["script_kw_with_display_name"] == 1
    assert out["script_kw_without_display_name"] == 0


# --------------------------------------------------------------------------
# 源码契约：两页都接上、与筛选同源、列不重复、溢出有出口
# --------------------------------------------------------------------------


def test_both_pages_mount_the_shared_tab_strip() -> None:
    """页签条是共用组件，两页都得挂上——用户就是在脚本工具页发现漏了的。"""
    for path in (REGISTRY, SCRIPT_LIST):
        source = _source(path)
        assert "import ToolGroupTabs from './ToolGroupTabs.vue'" in source, f"{path.name} 未引入页签组件"
        assert "<ToolGroupTabs" in source, f"{path.name} 未渲染页签组件"
        assert ':model-value="groupFilter"' in source, f"{path.name} 页签未绑定 groupFilter"
        assert '@update:model-value="groupFilter = $event"' in source, f"{path.name} 页签更新未回写"


def test_tab_component_is_stateless_about_the_selected_group() -> None:
    """组件只渲染与回传，不许自己存一份分组选择，否则与筛选下拉会各自为政。"""
    source = _source(TABS_COMPONENT)
    assert "modelValue" in source
    assert "emit('update:modelValue'" in source
    # 注释里提到 groupFilter 没关系（是在解释状态归父组件），这里锁的是「不许声明它」
    assert "const groupFilter" not in source, "页签组件不得自持分组状态"
    assert "groupFilter = ref(" not in source


def test_each_page_shares_group_filter_with_its_filter_panel() -> None:
    """页签与各自的筛选面板分组下拉共用同一个状态。"""
    registry = _source(REGISTRY)
    script = _source(SCRIPT_LIST)
    assert 'v-model="groupFilter"' in registry
    assert 'v-model="groupFilter"' in script
    for source in (registry, script):
        assert "groupTabFilter" not in source, "不得自建第二份分组状态"


def test_tab_counts_come_from_the_pure_module_not_full_totals() -> None:
    """页签计数必须是筛选后的命中数；用全量计数会显示完整数量，点进去才发现是空的。"""
    registry = _source(REGISTRY)
    script = _source(SCRIPT_LIST)
    assert "computeGroupTabs(tools.value, registryFilters.value, UNGROUPED_FILTER)" in registry
    assert "computeGroupTabs(tools.value, scriptFilters.value, UNGROUPED)" in script
    for source in (registry, script):
        assert "const groupTabItems = computed(() => groupTabCounts.value.items)" in source
        block_start = source.index("const groupTabCounts = computed(")
        block = source[block_start : block_start + 180]
        assert "computeGroupTabs(" in block
        assert "for (const tool" not in block, "组件里不该再长出一份分组聚合"
        assert "new Map<" not in block


def test_script_page_passes_its_own_search_dimensions() -> None:
    """脚本工具的筛选维度是「来源 + 状态 + 关键词（含 display_name）」，与 API 工具页不同。"""
    source = _source(SCRIPT_LIST)
    assert "source: sourceFilter.value" in source
    assert "searchFields: ['name', 'display_name', 'description']" in source


def test_group_column_hidden_while_a_group_tab_is_active() -> None:
    """页签已表明「在看哪一组」，表格不再重复一列。"""
    registry = _source(REGISTRY)
    script = _source(SCRIPT_LIST)
    assert '<th v-if="groupFilter === \'all\'" class="w-32' in registry
    assert '<td v-if="groupFilter === \'all\'" class="px-6 py-4 whitespace-nowrap text-sm">' in registry
    assert ':colspan="groupFilter === \'all\' ? 6 : 5"' in registry
    assert '<th v-if="groupFilter === \'all\'" class="w-[120px]' in script
    assert '<td v-if="groupFilter === \'all\'" class="px-3 py-3.5 text-[13px] text-gray-600"' in script


def test_overflow_has_a_visible_way_out() -> None:
    """分组多到放不下时：横向可滚 + 「更多分组」下拉，不能把页签截断在屏幕外。"""
    source = _source(TABS_COMPONENT)
    assert "scrollWidth - el.clientWidth > 4" in source
    assert "new ResizeObserver" in source
    assert "更多分组" in source
    assert "overflow-x-auto" in source


def test_stale_group_filter_falls_back_to_all() -> None:
    """当前分组被筛空或被删时回「全部」，否则页签消失 + 表格空白，看着像功能坏了。"""
    for path in (REGISTRY, SCRIPT_LIST):
        source = _source(path)
        assert "!items.some((item) => item.key === groupFilter.value)" in source, f"{path.name} 缺少兜底"
        assert "groupFilter.value = 'all'" in source
        assert "tools.value.length > 0" in source, f"{path.name} 兜底需避开加载期"
