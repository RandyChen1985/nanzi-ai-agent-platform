"""迁移脚本必须两套数据库都有。

MySQL 用 db-prod/，PostgreSQL 用 db-prod-pg/，两套不可混用。只写一套的话，
另一套环境的用户升级后会遇到「接口报 Table doesn't exist」——而代码已经上线了。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "fragment",
    ["credential-center", "credential-center-menu", "secret-audit-log"],
)
def test_both_dialects_have_the_migration(fragment):
    mysql = list((ROOT / "db-prod").glob(f"V*-{fragment}.sql"))
    pg = list((ROOT / "db-prod-pg").glob(f"V*-{fragment}.sql"))

    assert mysql, f"db-prod/ 缺少 V*-{fragment}.sql"
    assert pg, f"db-prod-pg/ 缺少 V*-{fragment}.sql"


def test_audit_migration_does_not_use_a_database_name():
    """脚本不指定库名，在哪个库执行就建在哪个库。

    写死库名的脚本在别人的环境里会建到错误的库，然后接口报 doesn't exist——
    两个库各自都对，只是不是同一个。
    """
    mysql = (ROOT / "db-prod/V211-add-secret-audit-log.sql").read_text(encoding="utf-8")

    assert "USE " not in mysql.upper().replace("-- ", "")
    assert "CREATE TABLE IF NOT EXISTS" in mysql


def test_audit_migration_has_no_foreign_key():
    """删凭据不删审计。"""
    mysql = (ROOT / "db-prod/V211-add-secret-audit-log.sql").read_text(encoding="utf-8")
    pg = (ROOT / "db-prod-pg/V111-add-secret-audit-log.sql").read_text(encoding="utf-8")

    for sql in (mysql, pg):
        lowered = sql.lower()
        # 注释里会出现「不设外键」，只看 DDL 部分
        ddl = "\n".join(
            line for line in lowered.splitlines() if not line.strip().startswith("--")
        )
        assert "foreign key" not in ddl


def test_audit_migration_covers_the_query_indexes():
    """主查询是「某条凭据的历史，倒序翻页」，必须有对应索引。"""
    mysql = (ROOT / "db-prod/V211-add-secret-audit-log.sql").read_text(encoding="utf-8")

    assert "ix_secret_audit_secret_created" in mysql
    assert "ix_secret_audit_actor_created" in mysql
