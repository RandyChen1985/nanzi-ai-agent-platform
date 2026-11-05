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
    """工具栏的时间筛选是日历面板（不是原生下拉），刷新按钮与它同一套描边风格。

    刷新原来是 ghost 按钮（透明底 + hover 蓝底），和旁边的日期卡片不搭；
    现在改成同一组的描边卡片：h-9 / rounded-xl / border-gray-200 / bg-white。
    """
    overview = (ROOT / "frontend/src/views/Overview.vue").read_text(encoding="utf-8")
    toolbar = overview.split("overview-toolbar", 1)[1].split("</header>", 1)[0]

    assert "overview-toolbar" in overview
    assert "<DateRangePicker" in overview
    assert "<select" not in overview
    assert "overview-refresh-btn" in overview
    assert "flex h-9 items-stretch overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm" in toolbar
    # ghost 风格已弃用
    assert "hover:bg-blue-50 hover:text-blue-600" not in toolbar
    assert "border border-gray-300 bg-white" not in toolbar


def test_deployment_check_is_folded_into_refresh_menu():
    """「部署检查」收进刷新按钮的下拉，不再占一个独立按钮。"""
    overview = (ROOT / "frontend/src/views/Overview.vue").read_text(encoding="utf-8")

    assert 'ref="refreshMenuRef"' in overview
    assert "@click=\"runDeploymentCheck\"" in overview
    assert "window.dispatchEvent(new CustomEvent(\"deployment-checklist:open\"))" in overview
    # compact 触发按钮已移除，详情区块保留
    assert "<DeploymentChecklist v-if=\"userInfo?.role === 'admin'\" compact />" not in overview
    assert "<DeploymentChecklist v-if=\"userInfo?.role === 'admin'\" hide-when-complete />" in overview


def test_overview_drops_the_subtitle():
    """标题下面的那句副标题已去掉，头部只留标题 + 工具栏。"""
    overview = (ROOT / "frontend/src/views/Overview.vue").read_text(encoding="utf-8")

    assert "平台运行状态与智能体健康度" not in overview
    assert "我的调用概况与智能体表现" not in overview
