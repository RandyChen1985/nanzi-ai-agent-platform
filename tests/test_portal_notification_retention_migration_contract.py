"""站内信保留期清理索引：MySQL V187 与 PostgreSQL V87 必须成对存在并与 ORM 一致。"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.models.portal_notification import PortalNotification
from app.services.ai import scheduler_service

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
MYSQL_MIGRATION = ROOT / "db-prod" / "V187-add_portal_notifications_created_at_index.sql"
PG_MIGRATION = (
    ROOT / "db-prod-pg" / "V87-add_portal_notifications_created_at_index.sql"
)
INDEX_NAME = "idx_portal_notifications_created_at"


def test_both_dialect_migrations_exist():
    assert MYSQL_MIGRATION.is_file(), "缺少 MySQL 迁移 V187"
    assert PG_MIGRATION.is_file(), "缺少 PostgreSQL 迁移 V87"


def test_mysql_migration_adds_single_column_created_at_index():
    sql = MYSQL_MIGRATION.read_text(encoding="utf-8")
    assert "ALTER TABLE `portal_notifications`" in sql
    assert f"ADD INDEX `{INDEX_NAME}` (`created_at`)" in sql
    # 原生 DDL 的幂等依据：apply_sql.py 跳过 1061 Duplicate key name
    assert "1061" in sql


def test_mysql_migration_is_idempotent_by_runner_allowlist():
    """重复执行靠 runner 跳过 1061，不能改写成 MariaDB 专有的 IF NOT EXISTS。"""
    runner = (ROOT / "db-prod" / "apply_sql.py").read_text(encoding="utf-8")
    assert "1061" in runner
    assert "IF NOT EXISTS" not in MYSQL_MIGRATION.read_text(encoding="utf-8")


def test_postgresql_migration_is_idempotent():
    sql = PG_MIGRATION.read_text(encoding="utf-8")
    assert f"CREATE INDEX IF NOT EXISTS {INDEX_NAME}" in sql
    assert "ON portal_notifications (created_at)" in sql


def test_orm_declares_the_same_index():
    """ORM 与迁移必须同名同列，否则全新库（靠迁移）与 ORM 元数据会漂移。"""
    index = next(
        (i for i in PortalNotification.__table__.indexes if i.name == INDEX_NAME), None
    )
    assert index is not None
    assert [column.name for column in index.columns] == ["created_at"]


def test_purge_statement_can_use_the_index():
    """清理 SQL 必须按 created_at 单列过滤，走这条索引；一旦改成函数包裹就会失效。"""
    source = inspect.getsource(
        scheduler_service.__dict__["_system_audit_log_maintenance_job"]
    )
    assert "purge_older_than" in source
    assert scheduler_service.PORTAL_NOTIFICATION_RETENTION_DAYS == 30

    from app.services.portal_notification_service import PortalNotificationService

    purge_source = inspect.getsource(PortalNotificationService.purge_older_than)
    assert "WHERE created_at < :threshold" in purge_source
