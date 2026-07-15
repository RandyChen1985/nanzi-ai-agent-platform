from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
SOURCE = (Path(__file__).resolve().parents[2] / "frontend/src/components/chatbi/DatasetCapabilityMenu.vue").read_text(encoding="utf-8")


def test_saved_report_owner_has_subscription_management_actions():
    for text in ("订阅设置", "保存订阅", "立即执行", "删除订阅", "失败时同时发送外部通知", "成功时发送站内及外部通知"):
        assert text in SOURCE
    for endpoint in ("/subscription`", "/subscription/${action}", "/subscription/run"):
        assert endpoint in SOURCE


def test_saved_report_subscription_supports_all_schedule_types():
    for value in ('value="daily"', 'value="weekly"', 'value="monthly"', 'value="cron"'):
        assert value in SOURCE
