"""Langfuse 配置表迁移脚本契约测试。

双库（MySQL ``db-prod/`` 与 PostgreSQL ``db-prod-pg/``）必须成对、幂等、且列集合一致；
PG 侧类型必须写成 PG 方言（布尔用 ``BOOLEAN``/``FALSE``，数值用 ``NUMERIC``，
时间用 ``TIMESTAMP``），否则在 PG 实例上会直接报语法错误。
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MYSQL_MIGRATION = REPO_ROOT / "db-prod" / "V161-create-langfuse-config.sql"
PG_MIGRATION = REPO_ROOT / "db-prod-pg" / "V62-create-langfuse-config.sql"

COLUMNS = (
    "id",
    "enabled",
    "host",
    "public_key",
    "secret_key",
    "sample_rate",
    "capture_content",
    "environment",
    "release",
    "timeout_seconds",
    "trace_url_template",
    "updated_by",
    "created_at",
    "updated_at",
)

PERMISSION_ID = "element:system:langfuse_save"


@pytest.fixture(scope="module")
def mysql_sql() -> str:
    assert MYSQL_MIGRATION.exists(), f"缺少迁移文件: {MYSQL_MIGRATION}"
    return MYSQL_MIGRATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pg_sql() -> str:
    assert PG_MIGRATION.exists(), f"缺少迁移文件: {PG_MIGRATION}"
    return PG_MIGRATION.read_text(encoding="utf-8")


def test_mysql_creates_table_idempotently(mysql_sql):
    assert "CREATE TABLE IF NOT EXISTS `langfuse_config`" in mysql_sql


def test_pg_creates_table_idempotently(pg_sql):
    normalized = pg_sql.replace('"', "`")
    assert "CREATE TABLE IF NOT EXISTS `langfuse_config`" in normalized


@pytest.mark.parametrize("column", COLUMNS)
def test_mysql_has_all_columns(mysql_sql, column):
    assert re.search(rf"`{re.escape(column)}`", mysql_sql), f"MySQL 迁移缺少列 {column}"


@pytest.mark.parametrize("column", COLUMNS)
def test_pg_has_all_columns(pg_sql, column):
    assert re.search(rf'"{re.escape(column)}"', pg_sql), f"PG 迁移缺少列 {column}"


def test_mysql_seeds_single_default_row(mysql_sql):
    assert re.search(r"INSERT\s+IGNORE\s+INTO\s+`langfuse_config`", mysql_sql)
    # 开关默认关闭、内容默认采集、超时默认 5
    assert re.search(r"VALUES\s*\(\s*1\s*,\s*0\s*,", mysql_sql)
    assert "1.000" in mysql_sql
    assert re.search(r",\s*1\s*,\s*5\s*\)", mysql_sql)


def test_pg_seeds_single_default_row(pg_sql):
    # PG 沿用项目既有惯例：INSERT ... SELECT ... WHERE NOT EXISTS 保证幂等。
    assert "WHERE NOT EXISTS" in pg_sql
    assert re.search(r"SELECT\s+1\s*,\s*FALSE\s*,", pg_sql)
    assert "TRUE" in pg_sql
    assert "1.000" in pg_sql
    # 默认行只允许存在一行：以 id = 1 作为存在性判据
    assert re.search(r'WHERE\s+"id"\s*=\s*1', pg_sql)


def test_mysql_uses_mysql_types(mysql_sql):
    assert "TINYINT(1)" in mysql_sql
    assert "DECIMAL(4,3)" in mysql_sql
    assert "DATETIME" in mysql_sql


def test_pg_uses_pg_types(pg_sql):
    assert "BOOLEAN" in pg_sql
    assert "NUMERIC(4,3)" in pg_sql
    assert "TIMESTAMP" in pg_sql
    # 混入 MySQL 方言会在 PG 上报语法错误
    assert "TINYINT" not in pg_sql
    assert "DATETIME" not in pg_sql
    assert "ENGINE=InnoDB" not in pg_sql


def test_mysql_registers_dedicated_permission(mysql_sql):
    assert PERMISSION_ID in mysql_sql
    assert "ai_agent_resource_permissions" in mysql_sql


def test_mysql_permission_insert_is_really_idempotent(mysql_sql):
    """权限插入必须真正幂等，不能用 ``INSERT IGNORE``。

    回归背景：``ai_agent_resource_permissions`` 上**没有** (resource_type, resource_id)
    唯一索引，``INSERT IGNORE`` 拦不住重复插入——重复执行迁移会不断追加同一行
    （线上已有 ``menu:dashboard`` 7 行之类的历史脏数据）。故必须用 ``WHERE NOT EXISTS``。
    """
    marker = "INSERT INTO `ai_agent_resource_permissions`"
    assert marker in mysql_sql, "权限注册语句缺失"
    permission_block = mysql_sql[mysql_sql.index(marker):]
    assert "WHERE NOT EXISTS" in permission_block
    assert not re.search(r"INSERT\s+IGNORE\s+INTO\s+`ai_agent_resource_permissions`", mysql_sql)


def test_pg_registers_dedicated_permission(pg_sql):
    assert PERMISSION_ID in pg_sql
    assert re.search(r'"ai_agent_resource_permissions"', pg_sql)
    assert "WHERE NOT EXISTS" in pg_sql


def test_both_migrations_carry_required_header(mysql_sql, pg_sql):
    for sql in (mysql_sql, pg_sql):
        assert "变更原因" in sql
        assert "需求背景" in sql
        assert "创建时间" in sql
        assert "创建人" in sql
        # 项目规范：迁移只生成、不代执行
        assert "不得" in sql and "自动执行" in sql


def test_both_migrations_are_non_destructive(mysql_sql, pg_sql):
    for sql in (mysql_sql, pg_sql):
        lowered = sql.lower()
        assert "drop table" not in lowered
        assert "drop column" not in lowered
        assert "delete from" not in lowered
        assert "truncate" not in lowered


def test_pg_and_mysql_column_sets_match(mysql_sql, pg_sql):
    mysql_columns = set(re.findall(r"`(\w+)`", mysql_sql.split("PRIMARY KEY")[0]))
    pg_columns = set(re.findall(r'"(\w+)"', pg_sql.split("PRIMARY KEY")[0]))
    assert set(COLUMNS) <= mysql_columns
    assert set(COLUMNS) <= pg_columns
