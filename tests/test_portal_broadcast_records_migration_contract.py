"""群发记录表：MySQL V218 与 PostgreSQL V118 必须成对存在，并与 ORM 列集合一致。

这两套迁移是「不可混用」的平行实现，历史上最容易出的问题是：
只在 MySQL 加了表、PostgreSQL 环境启动后 500；或者迁移与 ORM 漂移
（列名/类型不一致），全新库靠迁移建表、老库靠 ORM 读，两边对不上。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models.portal_broadcast import PortalBroadcastRecord

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
MYSQL_MIGRATION = ROOT / "db-prod" / "V218-create-portal-broadcast-records.sql"
PG_MIGRATION = ROOT / "db-prod-pg" / "V118-create-portal-broadcast-records.sql"
TABLE = "portal_broadcast_records"
INDEX = "idx_portal_broadcast_records_created"


@pytest.fixture(scope="module")
def mysql_sql() -> str:
    return MYSQL_MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pg_sql() -> str:
    return PG_MIGRATION.read_text(encoding="utf-8")


def test_both_dialect_migrations_exist() -> None:
    assert MYSQL_MIGRATION.is_file(), "缺少 MySQL 迁移 V218"
    assert PG_MIGRATION.is_file(), "缺少 PostgreSQL 迁移 V118"


def test_orm_table_name_matches_migrations(mysql_sql: str, pg_sql: str) -> None:
    assert PortalBroadcastRecord.__tablename__ == TABLE
    assert f"CREATE TABLE IF NOT EXISTS {TABLE}" in mysql_sql
    assert f"CREATE TABLE IF NOT EXISTS {TABLE}" in pg_sql


def test_every_orm_column_exists_in_both_migrations(
    mysql_sql: str, pg_sql: str
) -> None:
    """ORM 的每个属性/列名都要在两边出现，防止「代码要写一列、迁移没建」。"""
    for column in PortalBroadcastRecord.__table__.columns:
        names = {column.name, column.key}
        assert any(name in mysql_sql for name in names), f"MySQL 迁移缺少列 {column.name}"
        assert any(name in pg_sql for name in names), f"PostgreSQL 迁移缺少列 {column.name}"


def test_postgres_uses_jsonb_and_mysql_uses_json(mysql_sql: str, pg_sql: str) -> None:
    """两套方言的 JSON 类型不同名：MySQL 是 JSON，PostgreSQL 惯用 JSONB。"""
    for column in ("audience_user_ids", "audience_dept_ids", "audience_role_ids"):
        assert re.search(rf"{column}\s+JSON\b", mysql_sql), f"MySQL {column} 应为 JSON"
        assert re.search(rf"{column}\s+JSONB\b", pg_sql), f"PostgreSQL {column} 应为 JSONB"


def test_created_at_index_is_declared_on_both_sides(
    mysql_sql: str, pg_sql: str
) -> None:
    """历史列表按 created_at 倒序分页，两套都要有索引。"""
    assert INDEX in mysql_sql
    assert f"CREATE INDEX IF NOT EXISTS {INDEX}" in pg_sql
    assert "ON portal_broadcast_records (created_at)" in pg_sql


def test_migrations_are_rerunnable(mysql_sql: str, pg_sql: str) -> None:
    """线上迁移脚本会被重复执行（断点续跑），两条语句都必须幂等。"""
    assert "CREATE TABLE IF NOT EXISTS" in mysql_sql
    assert "CREATE TABLE IF NOT EXISTS" in pg_sql
    assert "CREATE INDEX IF NOT EXISTS" in pg_sql


def test_no_foreign_key_on_sender(mysql_sql: str, pg_sql: str) -> None:
    """发送者账号可能被删除，历史记录仍要可读 —— 刻意不建外键。"""
    assert "FOREIGN KEY" not in mysql_sql.upper()
    assert "REFERENCES" not in pg_sql.upper()
