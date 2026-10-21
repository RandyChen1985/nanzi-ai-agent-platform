"""个人技能分享迁移契约（MySQL V179 / PG V80）。

分享功能新增 `skill_shares` 一张表，两套迁移必须与 ORM 模型保持一致：
漏一列不会在 MySQL/PG 任一侧立刻炸，但会在某个环境里悄悄丢数据（例如
`content_sha256` 缺失导致接受校验失效）。这里钉住列集合、索引与
`accepted_skill_id` / `snapshot_path` 的可空性——终态清空快照依赖它们可空。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
MYSQL = ROOT / "db-prod" / "V179-create_skill_shares.sql"
PG = ROOT / "db-prod-pg" / "V80-create_skill_shares.sql"

REQUIRED_COLUMNS = (
    "id",
    "source_user_id",
    "source_skill_id",
    "name",
    "description",
    "message",
    "target_user_id",
    "status",
    "snapshot_path",
    "content_sha256",
    "file_count",
    "total_size",
    "accepted_skill_id",
    "created_at",
    "responded_at",
)


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sql_only(path: Path) -> str:
    """剥掉 `--` 注释行：注释里也会出现列名，会让「缺列」检查失去意义。"""
    return "\n".join(
        line for line in _sql(path).splitlines()
        if not line.strip().startswith("--")
    )


def test_both_migration_files_exist():
    assert MYSQL.is_file(), f"缺少 MySQL 迁移: {MYSQL}"
    assert PG.is_file(), f"缺少 PostgreSQL 迁移: {PG}"


@pytest.mark.parametrize("path", [MYSQL, PG])
def test_migration_is_idempotent(path: Path):
    sql = _sql_only(path)
    assert "CREATE TABLE IF NOT EXISTS skill_shares" in sql
    assert "DROP TABLE" not in sql.upper()


@pytest.mark.parametrize("path", [MYSQL, PG])
def test_migration_has_every_model_column(path: Path):
    sql = _sql_only(path)
    missing = [column for column in REQUIRED_COLUMNS if column not in sql]
    assert not missing, f"{path.name} 缺少列: {missing}"


@pytest.mark.parametrize("path", [MYSQL, PG])
def test_snapshot_and_accepted_id_stay_nullable(path: Path):
    """终态要清空 snapshot_path、并回填 accepted_skill_id，两者都不能 NOT NULL。"""
    sql = _sql_only(path)
    for column, type_sql in (
        ("snapshot_path", "VARCHAR(1024)"),
        ("accepted_skill_id", "VARCHAR(128)"),
    ):
        definitions = [
            line for line in sql.splitlines()
            if line.strip().startswith(f"{column} {type_sql}")
        ]
        assert definitions, f"{path.name} 未定义 {column}"
        for definition in definitions:
            assert "NOT NULL" not in definition, f"{path.name} 的 {column} 不应为 NOT NULL"


@pytest.mark.parametrize("path", [MYSQL, PG])
def test_indexes_cover_source_and_target_lookups(path: Path):
    sql = _sql_only(path)
    assert "idx_skill_shares_source" in sql
    assert "idx_skill_shares_target" in sql


def test_mysql_uses_inline_comments():
    sql = _sql_only(MYSQL)
    assert "COMMENT='" in sql


def test_pg_uses_comment_on_statements():
    sql = _sql_only(PG)
    assert 'COMMENT ON TABLE "skill_shares"' in sql
    assert "COMMENT ON COLUMN" in sql


# 物理外键由 tests/api/portal/test_user_delete_skill_shares.py 负责断言「必须没有」：
# 建了外键又没有级联，删除用户会直接撞 RESTRICT 报 500。
