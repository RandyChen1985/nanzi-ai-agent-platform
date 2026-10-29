"""知识库「业务归属部门」下拉的关键词搜索。

部门数一多，靠全角空格缩进一层层找不现实。这里锁住搜索能力本身，
以及两个容易做错的点：

1. 展示层级不能用物化 `path`——它存的是部门 id（`/1/7/23/`），给人看的是按名称拼的链；
2. 打开下拉要重置关键词，否则上次搜过的词还在，列表看起来像"部门没了"。
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


def test_both_modals_have_department_search_input(page):
    """新建与编辑两个弹窗各有一个搜索框。"""
    # 带 class 的才算模板上的容器（script 里的 querySelector 选择器不算）
    assert page.count("data-dept-menu class=") == 2
    assert page.count('placeholder="搜索部门"') == 2
    # 项目规范：搜索框统一用 type="search"，由浏览器提供原生清空按钮
    assert page.count('type="search"') >= 2


def test_options_are_filtered_by_keyword(page):
    assert "const deptSearchKeyword" in page
    assert "const deptSearching" in page
    # 过滤忽略缩进，用剥掉缩进后的名称做包含匹配
    assert "stripDeptIndent(option.label).toLowerCase().includes(kw)" in page


def test_search_shows_ancestor_path_not_materialized_path(page):
    """命中项要补父级名称路径；物化 path 是 id 链，不能直接显示。"""
    assert "const deptNamePath" in page
    assert "option.path.slice(0, -1).join(' / ')" in page
    # 物化 path 仅用于反查 id → 名称，不直接渲染
    assert "deptById.value.get(Number(seg))?.name" in page


def test_keyword_resets_when_menu_opens(page):
    assert "const toggleDeptMenu" in page
    assert "if (deptMenuOpen.value) deptSearchKeyword.value = ''" in page
    # 两处下拉按钮都走 toggleDeptMenu，不能再直接写取反
    assert page.count('@click="toggleDeptMenu"') == 2
    assert '@click="deptMenuOpen = !deptMenuOpen"' not in page


def test_empty_search_result_has_message(page):
    assert "没有匹配" in page


def test_search_input_is_inside_department_select_guard(page):
    """搜索框必须待在 `[data-dept-select]` 内，否则点它会触发"点空白收起"。"""
    guard_at = page.index("data-dept-select class=\"relative\"")
    menu_at = page.index("data-dept-menu", guard_at)
    search_at = page.index('placeholder="搜索部门"', menu_at)
    # 搜索框在下拉容器之后、且该容器仍在同一个 data-dept-select 块内
    assert guard_at < menu_at < search_at
