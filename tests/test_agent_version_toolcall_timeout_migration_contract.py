from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]


def test_mysql_version_timeout_migration_adds_nullable_long_timeout_column():
    source = (ROOT / "db-prod/V133-add-agent-version-toolcall-timeout.sql").read_text(encoding="utf-8")

    assert "ALTER TABLE `ai_agent_versions`" in source
    assert "ADD COLUMN `toolcall_timeout_seconds` INT NULL" in source
    assert "NULL 表示跟随全局" in source
    assert "1-86400" in source


def test_postgres_version_timeout_migration_adds_nullable_long_timeout_column():
    source = (ROOT / "db-prod-pg/V33-add-agent-version-toolcall-timeout.sql").read_text(encoding="utf-8")

    assert 'ALTER TABLE "ai_agent_versions"' in source
    # 迁移改为 IF NOT EXISTS，支持重复执行
    assert 'ADD COLUMN IF NOT EXISTS "toolcall_timeout_seconds" INTEGER NULL' in source
    assert "NULL 表示跟随全局" in source
    assert "1-86400" in source
