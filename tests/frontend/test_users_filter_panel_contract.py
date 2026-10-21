"""用户页筛选条：身份 / 状态 / 部门合并为一个筛选面板的前端契约（源码级）。

三个并排 select 在 1280px 下就会把「刷新 / 更多」挤到换行，而且它们本就是同一类
「缩小列表」的开关。合并后要守住的是：

1. 顶栏只留「搜索 + 筛选 + 刷新/更多」，旧的三个独立下拉全部消失；
2. 面板内按身份 / 状态 / 部门分组，组间用分隔线区分；
3. 按钮上带已选条件计数，激活时有主色提示；
4. 面板与「更多」菜单互斥，点击面板外关闭；
5. 三个筛选值仍驱动同一份请求参数——本次只改布局，不改筛选语义。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USERS_VUE = ROOT / "frontend/src/views/Users.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return USERS_VUE.read_text(encoding="utf-8")


def _toggle() -> str:
    return _source().split("const toggleFilterPanel = () => {", 1)[1].split(
        "const applyFilters", 1
    )[0]


def _apply() -> str:
    return _source().split("const applyFilters = () => {", 1)[1].split(
        "// 点当前已选项", 1
    )[0]


def _close_menus() -> str:
    return _source().split("const closeMenus = () => {", 1)[1].split(
        "// 筛选面板与「更多」菜单互斥", 1
    )[0]


def test_old_standalone_selects_are_gone():
    """三个独立下拉（身份/状态/部门：全部）合并后不得残留。"""
    source = _source()

    assert "身份：全部" not in source
    assert "状态：全部" not in source
    assert "部门：全部" not in source
    # 部门下拉改到面板内，仍保留树形缩进与「全部部门」选项
    assert "全部部门" in source


def test_filter_panel_has_grouped_sections_with_dividers():
    """面板内按组展示，组间有分隔线，而不是把三个 select 竖着堆一遍。"""
    source = _source()

    for group in ("身份", "状态", "部门"):
        assert f">{group}</p>" in source, f"筛选面板缺少「{group}」分组"

    assert source.count("border-t border-gray-100") >= 3, (
        "身份/状态/部门之间以及底部操作区要有分隔线"
    )


def test_filter_button_shows_active_count_and_mutex_menu():
    """筛选按钮带计数徽标；打开时收起「更多」菜单，避免两个浮层叠在一起。"""
    source = _source()

    assert 'v-if="activeFilterCount"' in source
    assert "已选 ${activeFilterCount} 项" in source

    toggle = _toggle()
    assert "showMoreMenu.value = false" in toggle
    assert "openRowMenuId.value = null" in toggle


def test_panel_closes_on_outside_click():
    """根节点的 closeMenus 要一并收起筛选面板。"""
    assert "showFilterPanel.value = false" in _close_menus()


def test_filters_still_drive_the_same_request_params():
    """布局合并不能顺手改掉筛选语义：三个值仍要传给列表接口。"""
    source = _source()
    fetch = source.split("const fetchUsers = async", 1)[1].split(
        "// --- 微信绑定视图", 1
    )[0]

    assert "params.role = roleFilter.value" in fetch
    assert "params.status = statusFilter.value" in fetch
    assert "params.dept_id" in source
    assert "params.include_sub" in source


def test_changing_a_filter_resets_to_first_page_and_refetches():
    apply = _apply()

    assert "page.value = 1" in apply
    assert "fetchUsers()" in apply
    # 点已选项不重复请求
    assert "if (roleFilter.value === value) return" in _source()
    assert "if (statusFilter.value === value) return" in _source()


def test_clear_action_lives_inside_the_panel():
    """外部的「清除」按钮已移除，清除入口收进面板底部。"""
    source = _source()
    panel = source.split("v-if=\"showFilterPanel\"", 1)[1].split("</div>\n      </div>", 1)[0]

    assert "清除全部" in panel
    assert '@click="resetFilters"' in panel
