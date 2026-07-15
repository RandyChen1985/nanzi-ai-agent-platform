from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db-prod/V98-create-saved-report-digest-deliveries.sql"
SUBSCRIPTION_MIGRATION = ROOT / "db-prod/V97-create-saved-report-subscriptions-and-inbox.sql"
MODEL = ROOT / "app/models/saved_report.py"
ENDPOINT = ROOT / "app/api/portal/endpoints/saved_reports.py"


def test_digest_migration_adds_ai_switch_and_delivery_audit_table():
    source = MIGRATION.read_text(encoding="utf-8")
    subscription_source = SUBSCRIPTION_MIGRATION.read_text(encoding="utf-8")

    assert "`ai_analysis_enabled` TINYINT(1) NOT NULL DEFAULT 1" in subscription_source
    assert "ai_analysis_enabled" not in source
    assert "ALTER TABLE `portal_saved_report_subscriptions`" not in source
    assert "CREATE TABLE IF NOT EXISTS `portal_saved_report_digest_deliveries`" in source
    for column in (
        "run_id", "subscription_id", "channel", "digest_payload", "title",
        "content", "status", "error_message", "ai_status", "sent_at",
    ):
        line = next(line for line in source.splitlines() if f"`{column}`" in line)
        assert "COMMENT '" in line


def test_digest_orm_and_subscription_api_expose_ai_switch():
    model = MODEL.read_text(encoding="utf-8")
    endpoint = ENDPOINT.read_text(encoding="utf-8")

    assert "class PortalSavedReportDigestDelivery(Base):" in model
    assert 'ai_analysis_enabled = Column(Boolean, nullable=False, default=True)' in model
    assert 'analysis_instruction = Column(Text, nullable=True)' in model
    assert "ai_analysis_enabled: bool = True" in endpoint
    assert "analysis_instruction: Optional[str]" in endpoint
    assert '"ai_analysis_enabled": bool(row.ai_analysis_enabled)' in endpoint
