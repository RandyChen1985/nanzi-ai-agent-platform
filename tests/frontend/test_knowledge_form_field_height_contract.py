"""新建/编辑弹窗里「业务归属部门」与「解析算法」两个字段的高度对齐。

原先左边是原生 `<select>`（单行 ~38px），右边是双行自定义下拉（~60px），
并排放在同一行 `grid grid-cols-2` 里就一高一低。改法是把部门选择也做成
同构的两行下拉：第一行部门名、第二行说明，高度自然一致。

顺带解决了「说明文字堆在控件下面把整行越撑越高」的问题。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

PAGE = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/views/KnowledgeBaseManagement.vue"
)


@pytest.fixture(scope="module")
def page() -> str:
    assert PAGE.exists()
    return PAGE.read_text(encoding="utf-8")


def test_both_modals_use_the_two_line_department_dropdown(page):
    # 新建 + 编辑各一处，不再有原生 <select>（外层的点击守卫也会命中该字符串，
    # 所以这里只数模板上的属性写法）
    assert page.count('<div data-dept-select class="relative">') == 2
    assert '<option :value="0">未关联部门</option>' not in page
    assert "v-for=\"dept in deptSelectOptions\"" not in page


def test_department_dropdown_renders_two_lines_like_chunk_method(page):
    """两行结构必须与「解析算法」一致，否则高度又对不上。"""
    assert "{{ selectedDeptLabel }}" in page
    assert "{{ deptHintText }}" in page
    # 第一行 text-sm、第二行 text-[11px]，与 chunk 下拉同款
    assert 'block text-sm font-semibold truncate' in page
    assert 'block text-[11px] truncate' in page


def test_hint_text_covers_every_scope_state(page):
    assert "const deptHintText = computed(" in page
    assert "仅「部门级」需要归属部门" in page
    assert "已选值会保留" in page
    assert "未选部门会退化为授权可见" in page
    assert "含下级部门，成员自动只读" in page
    # 未选部门时用告警色，避免"看起来已经生效"
    assert "const deptHintClass = computed(" in page
    assert "text-amber-600" in page


def test_selected_label_strips_tree_indent(page):
    """选项 label 用全角空格表示层级，回填到按钮上会整体右移。"""
    assert "const stripDeptIndent" in page
    assert "stripDeptIndent(hit.label)" in page
    assert "[　\\s]+" in page


def test_menu_state_is_closed_on_outside_click_and_modal_open(page):
    assert "target?.closest('[data-dept-select]')" in page
    # 打开新建 / 编辑弹窗时都要收起，避免"上次没关掉的菜单"
    assert page.count("deptMenuOpen.value = false") >= 3
