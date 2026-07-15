from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db-prod/V97-create-saved-report-subscriptions-and-inbox.sql"


def test_subscription_migration_creates_subscription_and_inbox_tables_with_comments():
    source = MIGRATION.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS `portal_saved_report_subscriptions`" in source
    assert "CREATE TABLE IF NOT EXISTS `portal_notifications`" in source
    assert "COMMENT='黄金报表定时订阅配置表'" in source
    assert "COMMENT='用户站内通知消息表'" in source
    assert "REFERENCES `portal_saved_reports` (`id`)" in source
    assert source.count("COLLATE=utf8mb4_0900_ai_ci") == 2


def test_subscription_migration_documents_every_business_column():
    source = MIGRATION.read_text(encoding="utf-8")

    for column in (
        "schedule_type", "cron_expr", "params", "notify_on_success",
        "ai_analysis_enabled", "analysis_instruction", "notify_on_failure", "external_channels", "consecutive_failures",
        "category", "level", "resource_type", "resource_id", "read_at",
    ):
        line = next(line for line in source.splitlines() if f"`{column}`" in line)
        assert "COMMENT '" in line
