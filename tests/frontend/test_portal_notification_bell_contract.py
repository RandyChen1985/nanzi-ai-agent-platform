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
    assert '@mouseleave="scheduleClose"' not in bell
    assert '@mouseenter="cancelScheduledClose"' not in bell
    assert "scheduleClose" not in bell
    assert "report_id" in bell and "resource_id" in bell


def test_manual_saved_report_runs_follow_notification_policy():
    scheduler = (ROOT / "app/services/ai/scheduler_service.py").read_text(encoding="utf-8")
    endpoint = (ROOT / "app/api/portal/endpoints/saved_reports.py").read_text(encoding="utf-8")
    assert 'trigger_label = "手动触发" if is_manual else "定时触发"' in scheduler
    assert "if subscription.notify_on_success:" in scheduler
    assert "if not is_manual and subscription.notify_on_success" not in scheduler
    assert "_saved_report_subscription_wrapper(row.id, is_manual=True)" in endpoint


def test_notification_bell_uses_visibility_aware_polling_and_manual_refresh():
    bell = (ROOT / "frontend/src/components/PortalNotificationBell.vue").read_text(encoding="utf-8")

    assert "60_000" in bell
    assert "30_000" in bell
    assert 'document.addEventListener("visibilitychange"' in bell
    assert 'window.addEventListener("focus"' in bell
    assert "document.hidden" in bell
    assert "refreshNotifications" in bell
    assert 'aria-label="刷新消息"' in bell
    assert "刷新中" in bell


def test_notification_bell_supports_single_delete_and_clear_read_with_confirmation():
    bell = (ROOT / "frontend/src/components/PortalNotificationBell.vue").read_text(encoding="utf-8")

    for text in ("删除消息", "清空已读", "确认删除这条消息", "确认清空已读消息", "confirmDeleteNotifications"):
        assert text in bell
    assert 'axios.delete(`/api/portal/inbox/${deleteTarget.value.id}`)' in bell
    assert 'axios.delete("/api/portal/inbox/read")' in bell


def test_notification_header_uses_two_rows_on_narrow_panels():
    bell = (ROOT / "frontend/src/components/PortalNotificationBell.vue").read_text(encoding="utf-8")

    assert "notification-header-main" in bell
    assert "notification-header-actions" in bell
    assert "whitespace-nowrap" in bell


def test_saved_report_notification_uses_runtime_open_message_without_reinitializing_chat():
    chat = (ROOT / "frontend/src/views/Chat.vue").read_text(encoding="utf-8")
    embed = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    drawer = (ROOT / "frontend/src/components/chatbi/DatasetPortalDrawer.vue").read_text(encoding="utf-8")
    menu = (ROOT / "frontend/src/components/chatbi/DatasetCapabilityMenu.vue").read_text(encoding="utf-8")

    assert "widgetReady" in chat
    assert "SAVED_REPORT_OPEN_EVENT" in chat
    assert "createSavedReportOpenMessage" in chat
    assert "sendSavedReportOpenRequest" in chat
    assert "open_request_id" in chat
    assert 'case "OPEN_SAVED_REPORT":' in embed
    open_case = embed.split('case "OPEN_SAVED_REPORT":', 1)[1].split("break;", 1)[0]
    assert "initChat" not in open_case
    assert ':focus-saved-report-request="savedReportFocusRequest"' in embed
    assert ':focus-saved-report-request="focusSavedReportRequest"' in drawer
    assert "focusSavedReportRequest?:" in drawer
    assert "focusSavedReportRequest?:" in menu
    assert "props.focusSavedReportRequest?.request_id" in menu
    assert "focusSavedReportTarget" in menu
    assert 'localStorage.getItem("portal_focus_saved_report")' not in menu
    assert ':key="$route.fullPath"' not in chat


def test_mark_read_failure_does_not_block_saved_report_navigation():
    bell = (ROOT / "frontend/src/components/PortalNotificationBell.vue").read_text(encoding="utf-8")

    assert "notificationTarget" in bell
    assert "catch (error)" in bell
    assert "console.warn" in bell
    assert "closeNotifications()" in bell
    assert "await router.push(notificationTarget)" in bell
    assert "publishSavedReportOpenRequest" in bell
    assert "createSavedReportOpenRequest" in bell
    assert "open_request_id" in bell


def test_ordinary_notification_opens_markdown_detail_while_saved_report_jumps():
    bell = (ROOT / "frontend/src/components/PortalNotificationBell.vue").read_text(encoding="utf-8")

    assert "isSavedReportNotification" in bell
    assert 'from "../utils/markdown"' in bell
    assert "renderMarkdown" in bell
    assert "detailItem" in bell
    assert "notification-detail-body" in bell
    assert 'v-html="detailHtml"' in bell
    assert "⭐ 黄金报表" in bell
    assert "openNotification" in bell
    assert "detailItem.value = item" in bell
    assert "if (!isSavedReportNotification(item)) {\n    closeNotifications();\n  }" in bell
    assert "未读" in bell
    assert "已读" in bell
    assert "applyLocal" in bell
