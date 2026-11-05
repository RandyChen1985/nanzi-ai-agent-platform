from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def test_overview_title_is_not_workbench_for_non_admin():
    """概览与「我的工作台」是两页；非管理员概览不得再叫「我的工作台」。"""
    overview = (ROOT / "frontend/src/views/Overview.vue").read_text(encoding="utf-8")
    router = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")

    assert 'userInfo?.role === "admin" ? "系统概览" : "我的概览"' in overview
    assert '"我的工作台"' not in overview
    assert "meta: { title: '概览', perm: 'menu:dashboard' }" in router


def test_overview_toolbar_uses_ghost_period_and_refresh():
    """工具栏的时间筛选是日历面板（不是原生下拉），刷新按钮保持 ghost 风格。"""
    overview = (ROOT / "frontend/src/views/Overview.vue").read_text(encoding="utf-8")

    assert "overview-toolbar" in overview
    assert "<DateRangePicker" in overview
    assert "<select" not in overview
    assert "overview-refresh-btn" in overview
    assert "hover:bg-blue-50 hover:text-blue-600" in overview
    assert "border border-gray-300 bg-white" not in overview.split("overview-toolbar", 1)[1].split("</header>", 1)[0]
