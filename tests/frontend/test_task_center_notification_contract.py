from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def test_task_center_modal_supports_visual_notification_channels():
    page = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
    tools = (ROOT / "app/services/ai/tools/task_manager_tools.py").read_text(encoding="utf-8")

    assert "结果通知" in page
    assert "notificationChannels" in page
    assert "notification_channels" in page
    assert "站内消息" in page
    assert "钉钉" in page
    assert "企业微信" in page
    assert "邮件" in page
    assert "promptOverlapsNotificationChannels" in page
    assert "isNotificationChannelReady" in page
    assert "/api/portal/notifications/config" in page
    assert "去个人中心配置消息通知" in page
    assert "tab: 'notifications'" in page
    assert "showPromptHelpModal" in page
    assert "要做什么 · 填写示例" in page
    assert "填入示例" in page or "填入" in page
    assert 'size="max-w-md"' in page
    assert "showAgentDropdown" in page
    assert "selectedEditingAgent" in page
    assert "SYSTEM" in page
    assert "notification_channels" in tools
    assert "merge_notification_channels_into_config" in tools


def test_task_center_saved_report_subscription_entry():
    page = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
    assert "订阅设置" in page
    assert "openSavedReportSubscriptionSettings" in page
    assert "report_detail_tab" in page


def test_task_center_cleans_resize_listener_and_run_now_timers():
    """resize 必须用同一命名 handler 解绑；runTaskNow 轮询定时器须在 onUnmounted 清理。"""
    page = (ROOT / "frontend/src/views/TaskCenter.vue").read_text(encoding="utf-8")
    assert "const handleWindowResize" in page
    assert "addEventListener('resize', handleWindowResize)" in page
    assert "removeEventListener('resize', handleWindowResize)" in page
    assert "() => windowWidth.value = window.innerWidth" not in page
    assert "clearRunNowTimers" in page
    assert "runNowTimerIds" in page
    assert "trackRunNowTimer" in page
    assert "releaseRunNowTimer" in page
