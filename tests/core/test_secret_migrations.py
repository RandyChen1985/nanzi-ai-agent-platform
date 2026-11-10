"""迁移脚本必须两套数据库都有。

MySQL 用 db-prod/，PostgreSQL 用 db-prod-pg/，两套不可混用。只写一套的话，
另一套环境的用户升级后会遇到「接口报 Table doesn't exist」——而代码已经上线了。

凭据中心是一个文件建完的（表 / 菜单 / 审计 / 工具参数标记 / 绑定唯一性）：
这些改动从未发布过，拆成多个只会在别人的环境里制造顺序依赖。所以这里的断言
是「同一份脚本里各节都在」，而不是「有几个文件」。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

MYSQL = ROOT / "db-prod/V209-add-credential-center.sql"
PG = ROOT / "db-prod-pg/V109-add-credential-center.sql"


def _ddl_only(sql: str) -> str:
    """去掉注释行，只留可执行语句。注释里会出现「不设外键」这类说明。"""
    return "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )


@pytest.mark.parametrize("path", [MYSQL, PG], ids=["mysql", "pg"])
def test_both_dialects_have_the_migration(path):
    assert path.exists(), f"缺少 {path}"


@pytest.mark.parametrize("path", [MYSQL, PG], ids=["mysql", "pg"])
def test_one_file_covers_every_part(path):
    """五节都在：建表、菜单、审计、工具参数标记、绑定唯一性。"""
    sql = path.read_text(encoding="utf-8")
    for table in (
        "sys_secrets",
        "sys_secret_grants",
        "sys_secret_bindings",
        "sys_secret_usage_logs",
        "sys_secret_user_prefs",
        "sys_secret_audit_logs",
    ):
        assert table in sql, f"{path.name} 缺少 {table}"
    assert "menu:system:credentials" in sql
    assert "credential_params" in sql
    assert "bound_by_user_id" in sql


@pytest.mark.parametrize("path", [MYSQL, PG], ids=["mysql", "pg"])
def test_migration_does_not_use_a_database_name(path):
    """脚本不指定库名，在哪个库执行就建在哪个库。

    写死库名的脚本在别人的环境里会建到错误的库，然后接口报 doesn't exist——
    两个库各自都对，只是不是同一个。
    """
    sql = path.read_text(encoding="utf-8")
    assert "USE " not in sql.upper().replace("-- ", "")


@pytest.mark.parametrize("path", [MYSQL, PG], ids=["mysql", "pg"])
def test_migration_is_idempotent(path):
    """必须能重复执行：本地已经跑过旧版迁移的库要能直接重跑这一份。"""
    sql = path.read_text(encoding="utf-8")
    ddl = _ddl_only(sql)
    # 建表一律 IF NOT EXISTS
    assert "CREATE TABLE `" not in ddl
    assert "CREATE TABLE sys_secret" not in ddl
    # 加列：MySQL 靠 apply_sql.py 忽略 1060，PG 必须显式 IF NOT EXISTS
    if "pg" in path.parent.name:
        assert "ADD COLUMN IF NOT EXISTS credential_params" in ddl
    # 菜单插入不能无条件
    assert "WHERE NOT EXISTS" in ddl


@pytest.mark.parametrize("path", [MYSQL, PG], ids=["mysql", "pg"])
def test_audit_table_has_no_foreign_key(path):
    """删凭据不删审计。"""
    ddl = _ddl_only(path.read_text(encoding="utf-8")).lower()
    assert "foreign key" not in ddl


@pytest.mark.parametrize("path", [MYSQL, PG], ids=["mysql", "pg"])
def test_audit_table_covers_the_query_indexes(path):
    """主查询是「某条凭据的历史，倒序翻页」，必须有对应索引。"""
    sql = path.read_text(encoding="utf-8")
    assert "ix_secret_audit_secret_created" in sql
    assert "ix_secret_audit_actor_created" in sql


@pytest.mark.parametrize("path", [MYSQL, PG], ids=["mysql", "pg"])
def test_global_binding_owner_becomes_empty_string(path):
    """全局绑定用空串：唯一索引含这一列，而 NULL 在唯一索引里互不相等。"""
    sql = path.read_text(encoding="utf-8")
    assert "SET bound_by_user_id = ''" in sql
    assert "WHERE bound_by_user_id IS NULL" in sql


def test_artifact_migration_stays_separate():
    """产物父会话是另一个主题，不该被并进凭据中心的迁移。

    编号紧跟在 V209 之后（V210 / PG V110）：中间空号会让人以为漏了文件。
    """
    assert (ROOT / "db-prod/V210-add-artifact-parent-conversation.sql").exists()
    assert (ROOT / "db-prod-pg/V110-add-artifact-parent-conversation.sql").exists()
