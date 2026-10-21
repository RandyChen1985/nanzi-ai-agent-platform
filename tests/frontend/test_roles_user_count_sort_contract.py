"""角色列表「用户数」列排序的前端契约（源码级）。

列表是服务端分页的，排序必须随请求下发（`sort_by` / `sort_order`），不能在前端
对 `roles` 数组本地排序——那样只能排当前页。这里锁定几条交互约束：

1. 「用户数」表头是真的按钮，并带 `aria-sort` 无障碍信息；
2. 默认列表顺序不变（创建时间倒序）；未按用户数排序时，首次点击默认倒序；
3. 再次点击在升/降序之间切换，并把页码重置到第 1 页；
4. 移动端没有表头，也要有等价的排序入口；
5. 请求真的把排序参数传给 `/api/portal/roles`。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES_VUE = ROOT / "frontend/src/views/Roles.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return ROLES_VUE.read_text(encoding="utf-8")


def _toggle() -> str:
    return _source().split("const toggleUserCountSort = () => {", 1)[1].split(
        "let searchTimeout", 1
    )[0]


def _fetch() -> str:
    return _source().split("const fetchRoles = async () => {", 1)[1].split(
        "// 用户数列头", 1
    )[0]


def _desktop_head() -> str:
    """桌面表头片段：从 thead 到 tbody，只看列头。"""
    return _source().split('<thead class="bg-gray-50">', 1)[1].split("<tbody", 1)[0]


def test_user_count_header_is_a_sort_button():
    head = _desktop_head()

    assert "用户数" in head
    assert 'aria-sort="userCountAriaSort"' in head, "排序列要给出 aria-sort，屏幕阅读器才知道当前方向"
    assert '@click="toggleUserCountSort"' in head, "用户数列头必须可点击"


def test_first_click_defaults_to_desc():
    toggle = _toggle()

    assert "sortBy.value !== 'user_count'" in toggle
    assert "sortBy.value = 'user_count'" in toggle
    assert "sortOrder.value = 'desc'" in toggle, "首次点「用户数」默认倒序（人多→人少）"


def test_toggle_flips_direction_and_resets_page():
    toggle = _toggle()

    assert "sortOrder.value === 'desc' ? 'asc' : 'desc'" in toggle, "再次点击要能切换升/降序"
    assert "page.value = 1" in toggle, "换排序后要回到第 1 页，否则会停在旧页码上看不到前几名"
    assert "fetchRoles()" in toggle


def test_default_order_stays_created_at():
    source = _source()

    assert "sortBy = ref<'created_at' | 'user_count'>('created_at')" in source, (
        "本次只增加可点击排序，默认列表顺序仍应保持创建时间倒序"
    )
    assert "sortOrder = ref<'asc' | 'desc'>('desc')" in source


def test_request_sends_sort_params_to_backend():
    fetch = _fetch()

    assert "sort_by: sortBy.value" in fetch
    assert "sort_order: sortOrder.value" in fetch
    assert "/api/portal/roles" in fetch
    # 不允许用本地 sort 对当前页数据排序充数
    assert "roles.value.sort" not in fetch


def test_mobile_has_equivalent_sort_entry():
    """移动端是卡片列表，没有表头，排序入口不能丢。"""
    source = _source()

    assert source.count('@click="toggleUserCountSort"') >= 2, "桌面表头与移动端排序条各需要一个入口"
    assert "移动端没有表头" in source or "移动端排序" in source or "排序</span>" in source


def test_sort_hint_explains_system_roles_are_pinned():
    """系统角色始终置顶，提示文案要说出来，否则用户会以为排序没生效。"""
    assert "系统角色始终置顶" in _source()
