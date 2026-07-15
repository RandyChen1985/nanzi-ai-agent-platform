from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
SOURCE = (Path(__file__).resolve().parents[2] / "frontend/src/components/chatbi/DatasetCapabilityMenu.vue").read_text(encoding="utf-8")
CARD = (Path(__file__).resolve().parents[2] / "frontend/src/components/chatbi/SavedReportItemCard.vue").read_text(encoding="utf-8")
BROWSER = (Path(__file__).resolve().parents[2] / "frontend/src/components/chatbi/SavedReportBrowseModal.vue").read_text(encoding="utf-8")


def test_saved_report_owner_has_subscription_management_actions():
    for text in ("订阅设置", "保存订阅", "立即执行", "删除订阅", "失败时同时发送外部通知", "运行成功后发送报表简报"):
        assert text in SOURCE
    for endpoint in ("/subscription`", "/subscription/${action}", "/subscription/run"):
        assert endpoint in SOURCE


def test_saved_report_subscription_supports_all_schedule_types():
    for value in ('value="daily"', 'value="weekly"', 'value="monthly"', 'value="cron"'):
        assert value in SOURCE


def test_saved_report_subscription_configures_mobile_ai_digest():
    assert "ai_analysis_enabled" in SOURCE
    assert "AI 智能分析" in SOURCE
    assert "运行成功后发送报表简报" in SOURCE
    assert "关闭后仍会发送数据摘要" in SOURCE


def test_saved_report_list_shows_subscription_badge_and_opens_subscription_tab():
    for text in ("已订阅", "已暂停", "订阅异常", "subscription_status", "subscription_next_run_at", "每天"):
        assert text in CARD
    assert "emit('subscription', report)" in CARD
    assert '@subscription="openSavedReportSubscription"' in SOURCE
    assert "syncSavedReportSubscriptionSummary" in SOURCE


def test_saved_report_lists_offer_subscribed_smart_filter_and_switch_to_my_scope():
    for surface in (SOURCE, BROWSER):
        assert 'value: "subscribed"' in surface
        assert "🔔" in surface
        assert "已订阅" in surface
        assert "subscription_status" in surface
    assert 'savedReportScope.value = "my"' in SOURCE
    assert 'browserScope.value = "my"' in BROWSER


def test_subscription_run_has_progress_feedback_and_delete_requires_confirmation():
    for text in (
        "savedReportSubscriptionRunning", "执行中...", "animate-spin",
        "showDeleteSubscriptionConfirm", "确认删除订阅", "不会删除黄金报表和运行历史",
        "savedReportSubscriptionDeleting",
    ):
        assert text in SOURCE
    assert '@click="showDeleteSubscriptionConfirm = true"' in SOURCE
    assert '@click="confirmDeleteSavedReportSubscription"' in SOURCE


def test_subscription_accepts_optional_ai_analysis_instruction():
    for text in ("analysis_instruction", "补充分析要求", "最多 500 字", "maxlength=\"500\"", "仅在开启 AI 智能分析后生效"):
        assert text in SOURCE


def test_subscription_ai_options_have_accessible_help_popovers():
    for text in (
        'aria-label="了解 AI 智能分析"', 'aria-label="了解补充分析要求"',
        "activeSubscriptionHelp", "移动端报表简报", "不能要求 AI 编造",
        '@mouseleave="closeSubscriptionHelp"',
    ):
        assert text in SOURCE
