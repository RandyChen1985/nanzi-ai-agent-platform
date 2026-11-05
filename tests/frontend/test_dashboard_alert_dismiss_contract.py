"""概览页异常摘要条「可关闭」的契约。

这条告警是常驻的：只要当天有失败就一直挂在首屏最好的位置。已经定位到
「就是 sub_agent_call 在报错、正在处理」的时候，它只会碍事，所以必须能关。

两个边界必须守住：
1. 关闭状态按「当天 + 统计周期」记——换周期看到的是另一份读数，不该一起被关掉；
   跨天更要重新出现，否则一次误关就再也看不到告警了。
2. 存储不可用（隐私模式）时不能抛错，只是关不住而已。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "frontend/src/components/dashboard/DashboardAlertStrip.vue"
PAGE = ROOT / "frontend/src/views/Overview.vue"


@pytest.fixture(scope="module")
def component() -> str:
    return COMPONENT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_strip_can_be_dismissed(component: str) -> None:
    """条上要有真的关闭按钮，点了整条消失。"""
    assert 'aria-label="关闭失败提示"' in component
    assert 'v-if="!dismissed"' in component
    assert "const dismiss = () => {" in component
    assert "@click=\"dismiss\"" in component


def test_dismiss_scope_is_day_and_period(component: str) -> None:
    """跨天或切周期必须重新出现：关闭只对当下这份读数有效。"""
    assert "ov:alert-dismissed:" in component
    assert "toLocaleDateString('sv-SE')" in component
    assert "${props.scope}" in component


def test_period_is_passed_from_the_page(page: str) -> None:
    """不传 scope 的话，切周期仍共用同一个 key，等于关一次全关。

    时间筛选改成日历后，scope 由 `period` 变成当前区间（预设 key 或 A~B），
    粒度更细：换到另一段区间就该重新提示。
    """
    assert page.count(':scope="rangeScope"') == 2  # 系统概览 + 我的概览


def test_storage_failure_does_not_break_page(component: str) -> None:
    """localStorage 抛错（隐私模式/配额满）只能降级，不能让页面白屏。"""
    assert "try {" in component
    assert "localStorage.getItem(storageKey.value)" in component
    assert "localStorage.setItem(storageKey.value, '1')" in component
