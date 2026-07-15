from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
SCHEDULER = ROOT / "app/services/ai/scheduler_service.py"


def test_saved_report_success_uses_mobile_digest_instead_of_row_count_reminder():
    source = SCHEDULER.read_text(encoding="utf-8")

    assert "build_deterministic_digest" in source
    assert "enrich_digest_with_ai" in source
    assert "render_mobile_markdown" in source
    assert "PortalSavedReportDigestDelivery" in source
    assert "本次查询返回 {run.row_count if run else 0} 行数据" not in source


def test_digest_delivery_audits_inbox_and_each_external_channel():
    source = SCHEDULER.read_text(encoding="utf-8")

    assert 'channel="inbox"' in source
    assert "for channel in subscription.external_channels or []:" in source
    assert "status=\"success\" if ok else \"failed\"" in source
    assert "error_message=None if ok else error" in source

