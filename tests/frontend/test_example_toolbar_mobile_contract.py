"""案例集管理工具栏的移动端布局契约。

改造前是 flex-col + 四个散落的按钮：导出、检索测试各自占一行，同步按钮的
`w-full` 又被拉成一条只有图标的空条。现在窄屏固定两行——搜索框一行，
「分类下拉 + 四个 36px 操作图标」一行；lg 起恢复原来的单行工具栏。
"""

from __future__ import annotations

from pathlib import Path

import pytest

VIEW = Path("frontend/src/views/ExampleManagement.vue")
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _toolbar(source: str) -> str:
    start = source.index("flex w-full flex-col gap-2.5")
    end = source.index("<!-- 案例集全生命周期指引横幅 -->", start)
    return source[start:end]


def test_toolbar_is_search_row_plus_action_row_below_lg():
    source = _source()

    assert "flex w-full flex-col gap-2.5 lg:w-auto lg:flex-row lg:items-center" in source
    assert 'class="relative w-full lg:w-80"' in source

    toolbar = _toolbar(source)
    # 刷新 / 导出 / 检索 / 一键同步：四个同尺寸方形图标
    assert toolbar.count("inline-flex h-9 w-9 shrink-0") == 4
    # 图标按钮没有可见文字，必须带可读名称
    assert toolbar.count('aria-label="') >= 3


def test_sync_button_no_longer_stretches_into_an_empty_bar():
    toolbar = _toolbar(_source())

    # 旧写法在窄屏会铺满整行，只剩一个居中的云图标
    assert "inline-flex w-full items-center justify-center gap-1.5" not in toolbar
    # 文字只在 lg 起出现，窄屏靠图标 + aria-label
    assert '<span class="hidden lg:inline">同步</span>' in toolbar


def test_category_select_is_capped_so_tablet_does_not_get_a_giant_dropdown():
    source = _source()

    # flex-1 不限宽时，800px 下下拉会被拉到四五百像素
    assert "max-w-[200px] flex-1" in source
    assert "lg:max-w-none lg:flex-none" in source
