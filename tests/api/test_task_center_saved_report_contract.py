from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def test_task_api_exposes_saved_report_subscription_operations():
    source = (ROOT / "app/api/v1/endpoints/tasks.py").read_text(encoding="utf-8")
    for route in (
        '"/report-subscriptions"',
        '"/report-subscriptions/{subscription_id}/status"',
        '"/report-subscriptions/{subscription_id}/run"',
        '"/report-subscriptions/{subscription_id}"',
    ):
        assert route in source
    assert '"task_type": "saved_report"' in source


def test_task_center_frontend_routes_actions_by_task_type():
    api = (ROOT / "frontend/src/api/task.ts").read_text(encoding="utf-8")
    view = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
    assert "listReportSubscriptions" in api
    assert "runReportSubscription" in api
    assert "task.task_type === 'saved_report'" in view
    assert "报表订阅" in view
    assert "openSavedReportTask" in view
    assert "openSavedReportSubscriptionSettings" in view


def test_task_center_has_type_tabs_with_counts():
    view = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
    assert "taskTypeFilter" in view
    assert "taskTypeTabs" in view
    assert "全部任务" in view
    assert "智能体任务" in view
    assert "报表订阅" in view
    assert "taskTypeCounts" in view


def test_report_subscription_rows_use_readable_schedule_and_actions():
    """报表订阅行要展示可读周期、可读下次运行时间，并保留专属操作入口。

    2026-11-23「我的任务」重构：`formatNextRunCompact` 被 `nextRunCell` 取代
    （后者额外区分「暂停 → 已暂停」与「无计划」），断言随之换成新函数；
    「可读周期 + 可读下次运行 + 订阅专属操作」这组不变量不变。
    """
    view = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
    assert "formatTaskSchedule" in view
    assert "nextRunCell" in view
    assert "每天" in view
    assert "订阅设置" in view
    assert "report_detail_tab" in view
    assert "运行历史" in view
    assert "立即执行" in view
    assert "报表订阅专属操作" in view
