"""说明气泡：解释"人数为什么对不上"，且不许用原生 tooltip。

部门树统计显示「6 个部门 · 7 人」，但树上徽标是 1+4+3+0=8。数字对不上会让管理员怀疑
功能算错——必须先讲清口径（按用户去重，一人多部门只计一次）。

为什么不用原生 `title`：约 1 秒才出现、不能换行、样式不可控、**触摸设备不显示**。
这种"必须让人看到否则会误判"的解释，用原生 title 等于没写。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
TOOLTIP = ROOT / "frontend/src/components/InfoTooltip.vue"
PAGE = ROOT / "frontend/src/views/DepartmentManagement.vue"


def test_tooltip_is_not_native():
    src = TOOLTIP.read_text(encoding="utf-8")
    assert 'title="' not in src, "原生 title 不能用（延迟长、不可换行、触摸端不显示）"
    assert "role=\"tooltip\"" in src


def test_tooltip_is_reachable_by_keyboard_and_touch():
    src = TOOLTIP.read_text(encoding="utf-8")
    assert "<button" in src, "必须是可聚焦按钮，键盘 Tab 才能触发"
    assert "aria-label" in src
    assert "aria-expanded" in src
    assert "pinned" in src, "点击固定：触摸设备没有 hover"


def test_tooltip_supports_wrapping_for_long_text():
    """说明文案不可能一行放下，必须有固定宽度让它在框内换行。"""
    src = TOOLTIP.read_text(encoding="utf-8")
    assert "w-64" in src


def test_page_explains_the_dedup_math():
    src = PAGE.read_text(encoding="utf-8")
    assert "InfoTooltip" in src
    assert "去重" in src, "必须写明是去重口径"
    # 解释挂在统计旁，且只在有人数时出现
    assert 'v-if="memberTotal"' in src


def test_page_does_not_use_native_title_for_the_count():
    """统计旁的解释不能退化成原生 title。"""
    src = PAGE.read_text(encoding="utf-8")
    stat_block = src.split("组织架构", 1)[1].split("<div", 1)[0]
    assert "title=" not in stat_block
