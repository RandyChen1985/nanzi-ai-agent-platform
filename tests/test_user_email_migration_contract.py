"""邮箱字段迁移契约：钉住双库各自的幂等写法，避免两套迁移混用或漏建唯一索引。"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
MYSQL = ROOT / "db-prod" / "V163-add-user-email.sql"
PG = ROOT / "db-prod-pg" / "V64-add-user-email.sql"


def test_both_migration_files_exist():
    assert MYSQL.is_file(), f"缺少 MySQL 迁移: {MYSQL}"
    assert PG.is_file(), f"缺少 PG 迁移: {PG}"


def test_mysql_adds_column_after_real_name_with_comment():
    sql = MYSQL.read_text(encoding="utf-8")
    ddl = _ddl_only(sql)
    assert "ALTER TABLE" in ddl and "ai_agent_users" in ddl
    assert "ADD COLUMN" in ddl and "`email`" in ddl
    assert "AFTER `real_name`" in ddl, "email 应紧跟在 real_name 之后"
    assert "VARCHAR(254)" in ddl
    # 必须断言 DDL 而不是全文：注释里为了解释「多 NULL 不冲突」本来就含 "NULL"，
    # 用全文断言的话，把 DDL 改成 NOT NULL 这条用例依然会通过（假绿）。
    assert "NOT NULL" not in ddl, "email 必须允许为 NULL（多用户不填时不能互相冲突）"


def _ddl_only(sql: str) -> str:
    """剥掉 `--` 注释行，只留可执行 DDL。

    契约断言必须只看 DDL：注释里为了解释约定免不了提到 "IF NOT EXISTS"，
    把注释算进来会让一条纯文档性的改动就弄挂断言（这个坑第一次写时就踩了）。
    """
    return "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))


def test_mysql_creates_unique_index():
    sql = MYSQL.read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX" in sql
    assert "uk_ai_agent_users_email" in sql

    # 必须检查**全部**可执行语句，不能只看第一条（ALTER）：本文件有两条语句，
    # 只查第一条的话，把索引那条改成 `CREATE UNIQUE INDEX IF NOT EXISTS`
    # （MySQL 8.0 报 1064，且不在 apply_sql.py 的白名单里 → 迁移直接失败）
    # 用例依然会通过。
    ddl = _ddl_only(sql)
    assert "IF NOT EXISTS" not in ddl, (
        "MySQL 8.0 不支持 ADD COLUMN/CREATE INDEX IF NOT EXISTS；"
        "幂等由 apply_sql.py 吞掉 1060/1061 实现"
    )


def test_pg_uses_native_if_not_exists():
    sql = PG.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS" in sql, "PG 必须用原生 IF NOT EXISTS 保持真幂等"
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
    assert "uk_ai_agent_users_email" in sql
    assert '"email"' in sql and "VARCHAR(254)" in sql


def test_pg_does_not_mix_mysql_syntax():
    sql = PG.read_text(encoding="utf-8")
    assert "`" not in sql, "PG 迁移不能出现 MySQL 反引号"
    assert "INSERT IGNORE" not in sql


# --------------------------------------------------------------------------- #
# 邮件服务配置种子（V164 / V65）
# --------------------------------------------------------------------------- #

MYSQL_CFG = ROOT / "db-prod" / "V164-add-email-service-configs.sql"
PG_CFG = ROOT / "db-prod-pg" / "V65-add-email-service-configs.sql"

EMAIL_CONFIG_KEYS = [
    "email_service_enabled",
    "email_smtp_host",
    "email_smtp_port",
    "email_smtp_security",
    "email_smtp_user",
    "email_smtp_password",
    "email_from_address",
    "email_sender_name",
]


def test_email_config_migrations_exist():
    assert MYSQL_CFG.is_file()
    assert PG_CFG.is_file()


@pytest.mark.parametrize("path", [MYSQL_CFG, PG_CFG])
def test_email_config_seeds_all_keys_in_email_category(path):
    sql = path.read_text(encoding="utf-8")
    for key in EMAIL_CONFIG_KEYS:
        assert key in sql, f"{path.name} 缺少配置项 {key}"
    assert sql.count("'email'") >= len(EMAIL_CONFIG_KEYS), "所有项都必须是 email 分组"
    assert "is_secret" in sql


@pytest.mark.parametrize("path", [MYSQL_CFG, PG_CFG])
def test_smtp_password_is_secret_and_switch_defaults_off(path):
    sql = path.read_text(encoding="utf-8")
    # 密码行必须标记为密钥（平台首个 is_secret 使用者）
    pw_line = [l for l in sql.splitlines() if "email_smtp_password" in l]
    assert pw_line, "缺少密码配置项"
    assert any(("TRUE" in l or "1" in l) for l in pw_line), "密码必须 is_secret=1/TRUE"
    # 总开关默认关闭
    sw_line = [l for l in sql.splitlines() if "email_service_enabled" in l]
    assert sw_line and any("false" in l.lower() for l in sw_line), "总开关默认必须是 false"


def test_mysql_config_seed_is_idempotent():
    sql = MYSQL_CFG.read_text(encoding="utf-8")
    assert "INSERT IGNORE" in sql
    assert "UPDATE" not in sql.upper().replace("UPDATED_AT", "")


def test_pg_config_seed_is_idempotent():
    sql = PG_CFG.read_text(encoding="utf-8")
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql
