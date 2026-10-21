"""个人 MCP 分享迁移契约（MySQL V181 / PostgreSQL V82）。"""
from pathlib import Path

import pytest

from app.models.mcp_share import McpServerShare

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
MYSQL = ROOT / "db-prod" / "V181-create_mcp_server_shares.sql"
PG = ROOT / "db-prod-pg" / "V82-create_mcp_server_shares.sql"


def _sql_only(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("--")
    )


def test_migration_files_exist():
    assert MYSQL.is_file(), f"缺少 MySQL 迁移: {MYSQL}"
    assert PG.is_file(), f"缺少 PostgreSQL 迁移: {PG}"


@pytest.mark.parametrize("path", [MYSQL, PG])
def test_migrations_create_table_with_columns(path: Path):
    sql = _sql_only(path)
    assert "CREATE TABLE IF NOT EXISTS mcp_server_shares" in sql
    for column in (
        "source_user_id",
        "source_server_id",
        "server_name",
        "sse_url",
        "payload",
        "include_credentials",
        "target_user_id",
        "status",
        "message",
        "accepted_server_id",
    ):
        assert column in sql, f"{path.name} 缺少列 {column}"


@pytest.mark.parametrize("path", [MYSQL, PG])
def test_migrations_create_both_indexes(path: Path):
    sql = _sql_only(path)
    assert "idx_mcp_shares_source" in sql
    assert "idx_mcp_shares_target" in sql


@pytest.mark.parametrize("path", [MYSQL, PG])
def test_migrations_skip_physical_foreign_keys(path: Path):
    """与 skill_shares 一致：不建物理外键，删除用户由服务层清理，否则删用户会 500。"""
    sql = _sql_only(path)
    assert "REFERENCES" not in sql.upper()
    assert "FOREIGN KEY" not in sql.upper()


def test_pg_migration_documents_table():
    sql = _sql_only(PG)
    assert 'COMMENT ON TABLE "mcp_server_shares"' in sql
    assert "COMMENT ON COLUMN" in sql


def test_model_matches_table():
    assert McpServerShare.__tablename__ == "mcp_server_shares"
    columns = {column.name for column in McpServerShare.__table__.columns}
    assert {
        "id",
        "source_user_id",
        "source_server_id",
        "server_name",
        "remark",
        "sse_url",
        "payload",
        "include_credentials",
        "target_user_id",
        "status",
        "message",
        "accepted_server_id",
        "created_at",
        "responded_at",
    } <= columns
    # 终态会清空 payload，必须可空
    assert McpServerShare.__table__.columns["payload"].nullable is True
