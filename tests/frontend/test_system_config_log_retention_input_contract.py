"""「日志保留天数」输入组的样式契约。

背景：这是全仓唯一一处「输入框 + 单位后缀」组合（`border-l-0` 在 frontend/src 下仅此
一处出现），没有既有规范可循，因此更需要用契约把「为什么这么写」锁住，防止后人又改回
不自洽的写法。

原始实现的问题（两个层次）：
1. **边框错位（客观缺陷）**：输入框只写了 `border-gray-300`（边框**颜色**）却漏了
   `border`（边框**宽度**）。Tailwind preflight 会把所有元素的 `border-width` 归零，
   于是输入框实际上**没有边框**，只靠 `shadow-sm` 描了一圈淡影；而后缀 `天` 却带着
   1px 硬边框。两者相接处一边是阴影、一边是实线，深浅与粗细都对不上，读起来像一颗
   浮在输入框右端的小胶囊，而不是同一个控件。
2. **数字与单位被拉散（观感割裂）**：`w-full` 让 2 位数字孤零零贴在左端、单位被推到
   最右，中间是一大片空灰底，二者读起来是两个不相干的东西。

本次调整：输入框补上真实边框（其右边框即为与单位之间的分隔线）、去掉输入框自带的
`shadow-sm`（避免与容器 shadow 叠加成双层描边）、数字改右对齐并使用等宽数字，让
「数值 + 单位」紧邻成一个整体。单位后缀补 `shrink-0` 防止被挤压变形。
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "frontend/src/views/SystemConfig.vue"
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return VIEW.read_text(encoding="utf-8")


def _retention_group(source: str) -> str:
    """截取「日志保留天数」这一组控件（label → 说明文案之前）。"""
    start = source.index("日志保留天数")
    end = source.index("* 日志超出天数后", start)
    return source[start:end]


def _input_element(group: str) -> str:
    start = group.index("<input")
    return group[start : group.index("/>", start)]


def _suffix_element(group: str) -> str:
    start = group.index("<span")
    return group[start : group.index(">", start)]


def _class_attr(element: str) -> str:
    start = element.index('class="') + len('class="')
    return element[start : element.index('"', start)]


def test_number_input_carries_a_real_border_width_not_just_a_color():
    """输入框必须有 `border` 宽度类，否则边框宽度为 0、与后缀的实线边框错位。"""
    classes = _class_attr(_input_element(_retention_group(_source())))

    assert "border-gray-300" in classes, "保留边框颜色"
    assert " border " in f" {classes} ", "必须显式声明 border 宽度类（Tailwind preflight 默认 border-width: 0）"


def test_input_does_not_double_the_group_shadow():
    """容器已有 shadow-sm，输入框再带 shadow-sm 会在接缝处叠出双层描边。"""
    group = _retention_group(_source())

    assert 'class="mt-1 flex rounded-md shadow-sm"' in group, "外层容器保留统一的圆角与描边阴影"
    assert "shadow-sm" not in _class_attr(_input_element(group)), "输入框不应再自带 shadow-sm"


def test_number_is_adjacent_to_its_unit():
    """数字右对齐并采用等宽数字，避免 2 位数字与单位被整行拉开。"""
    classes = _class_attr(_input_element(_retention_group(_source())))

    assert "text-right" in classes, "数值应右对齐、紧贴单位"
    assert "tabular-nums" in classes, "等宽数字，避免位数变化时抖动"
    assert "min-w-0" in classes, "flex 子项需允许收缩，防止窄屏溢出"


def test_input_and_suffix_share_one_rounded_outline():
    """左右两半圆角互补，后缀不画左边框（借用输入框的右边框当分隔线）。"""
    group = _retention_group(_source())
    input_classes = _class_attr(_input_element(group))
    suffix_classes = _class_attr(_suffix_element(group))

    assert "rounded-l-md" in input_classes
    assert "rounded-r-md" in suffix_classes
    assert "border-l-0" in suffix_classes, "后缀左侧不重复画线，避免分隔线变粗"
    assert "border" in suffix_classes.split(), "后缀需要上/右/下边框（借用输入框右边框当分隔线）"
    assert "shrink-0" in suffix_classes, "单位后缀不应被压缩变形"
    assert "天" in group
