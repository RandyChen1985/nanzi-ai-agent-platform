from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_mounts_persistent_notification_bell():
    dashboard = (ROOT / "frontend/src/views/Dashboard.vue").read_text(encoding="utf-8")
    bell = (ROOT / "frontend/src/components/PortalNotificationBell.vue").read_text(encoding="utf-8")
    assert "PortalNotificationBell" in dashboard
    assert "/api/portal/inbox/unread-count" in bell
    assert "/api/portal/inbox/read-all" in bell
    assert "全部已读" in bell
    assert "关闭通知" in bell
    assert '@mouseleave="scheduleClose"' in bell
    assert '@mouseenter="cancelScheduledClose"' in bell
    assert "report_id" in bell and "resource_id" in bell


def test_manual_saved_report_runs_follow_notification_policy():
    scheduler = (ROOT / "app/services/ai/scheduler_service.py").read_text(encoding="utf-8")
    endpoint = (ROOT / "app/api/portal/endpoints/saved_reports.py").read_text(encoding="utf-8")
    assert 'trigger_label = "手动触发" if is_manual else "定时触发"' in scheduler
    assert "if subscription.notify_on_success:" in scheduler
    assert "if not is_manual and subscription.notify_on_success" not in scheduler
    assert "_saved_report_subscription_wrapper(row.id, is_manual=True)" in endpoint
