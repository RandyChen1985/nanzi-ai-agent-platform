"""账号自主注册申请开关的双库迁移契约。

约束：
- MySQL / PostgreSQL 两侧都必须存在同一条配置种子；
- 默认值是 'false'（默认关闭注册功能）；
- 分类必须是 general（系统配置页「常规设置」分组）；
- 必须是幂等写入且不覆盖已存在的行，避免部署时把管理员已开启的开关改回 false。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]

MYSQL_MIGRATION = "db-prod/V162-add-user-self-registration-config.sql"
PG_MIGRATION = "db-prod-pg/V63-add-user-self-registration-config.sql"
CONFIG_KEY = "user_registration_enabled"


def test_mysql_migration_seeds_registration_switch_disabled_by_default():
    source = (ROOT / MYSQL_MIGRATION).read_text(encoding="utf-8")

    assert CONFIG_KEY in source
    assert "'false'" in source
    assert "'general'" in source
    assert "INSERT IGNORE" in source.upper()


def test_mysql_migration_does_not_overwrite_existing_value():
    source = (ROOT / MYSQL_MIGRATION).read_text(encoding="utf-8").upper()

    # 全新配置项：只做 INSERT IGNORE，不出现任何 UPDATE 子句
    assert "ON DUPLICATE KEY UPDATE" not in source
    assert "UPDATE " not in source


def test_postgres_migration_seeds_registration_switch_disabled_by_default():
    source = (ROOT / PG_MIGRATION).read_text(encoding="utf-8")

    assert CONFIG_KEY in source
    assert "'false'" in source
    assert "'general'" in source
    assert "ON CONFLICT" in source.upper()


def test_postgres_migration_does_not_overwrite_existing_value():
    source = (ROOT / PG_MIGRATION).read_text(encoding="utf-8").upper()

    # 全新配置项：冲突时什么都不做，绝不更新 value
    assert "DO NOTHING" in source
    assert "DO UPDATE" not in source
