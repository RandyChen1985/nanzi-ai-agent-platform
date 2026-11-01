"""任务调度台筛选控件（原生 select）的样式契约。

原生 select 的浏览器箭头跟这排圆角控件不是一套语言，宽度与高度也由 UA 决定：
箭头区宽度不受控、高度随行高浮动，跟左侧 38px 的搜索框对不齐。这里统一关掉原生
外观、换成与搜索框图标同粗细的单箭头，并锁死高度。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "frontend/src/views/TaskCenter.vue"


def _view() -> str:
    return VIEW.read_text(encoding="utf-8")


def test_all_filter_selects_use_the_shared_class():
    """任务列表的状态筛选 + 执行记录的状态/任务筛选，三处共用一套外观。"""
    source = _view()
    assert source.count("task-filter-select") == 4  # 3 处使用 + 1 处定义
    for model in ("statusFilter", "historyStatus", "historyTaskId"):
        start = source.index(f'v-model="{model}"')
        line = source[start: source.index("\n", start) + 400]
        assert "task-filter-select" in line, f"{model} 未套用统一外观"


def test_native_appearance_is_disabled_with_custom_arrow():
    source = _view()
    start = source.index(".task-filter-select {")
    block = source[start: source.index("}", start)]
    assert "appearance: none;" in block
    assert "-webkit-appearance: none;" in block
    assert "data:image/svg+xml" in block
    assert "d='M6 9l6 6 6-6'" in block
    assert "background-repeat: no-repeat;" in block


def test_height_matches_the_search_input():
    """`py-2 + text-sm` 的输入框是 38px，下拉必须锁同一高度才对得齐基线。"""
    source = _view()
    start = source.index(".task-filter-select {")
    block = source[start: source.index("}", start)]
    assert "height: 38px;" in block
    # 箭头区留白写死在类里，不依赖 UA
    assert "padding: 0 2rem 0 0.75rem;" in block


def test_padding_lives_in_the_class_not_in_utilities():
    """全局 base 层给 select 兜了 `padding: 8px 12px`；组件样式未分层、优先级更高，
    所以 padding 必须在类里一次说清，避免和 Tailwind 的 px/py 互相覆盖。"""
    source = _view()
    for model in ("statusFilter", "historyStatus", "historyTaskId"):
        start = source.index(f'v-model="{model}"')
        tag = source[start: source.index(">", start)]
        assert "px-2.5" not in tag and "py-2" not in tag, f"{model} 仍带 Tailwind 内边距"


def test_focus_state_matches_the_search_input():
    """聚焦态与搜索框一致：主色边框 + 2px 光晕。"""
    source = _view()
    for model in ("statusFilter", "historyStatus", "historyTaskId"):
        start = source.index(f'v-model="{model}"')
        tag = source[start: source.index(">", start)]
        assert "focus:border-primary" in tag, f"{model} 缺聚焦边框"
        assert "focus:ring-2 focus:ring-primary/20" in tag, f"{model} 缺聚焦光晕"


def test_transition_is_color_only():
    """只过渡颜色：过渡 padding/appearance 会在切换时抖动。"""
    source = _view()
    start = source.index('v-model="statusFilter"')
    tag = source[start: source.index(">", start)]
    assert "transition-colors" in tag
    assert "transition-all" not in tag
