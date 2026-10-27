"""范围门禁的三个配置项必须同时落进 MySQL 与 PostgreSQL 两套迁移。

代码里三个值都有兜底，所以迁移漏了也不会报错——只会让管理员在系统配置页里
找不到这一组配置。这类「静默缺失」只能靠契约测试盯住。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]

# (迁移文件, 标识符引用符)：MySQL 用反引号，PostgreSQL 用双引号
MIGRATIONS = (
    ("db-prod/V193-add_knowledge_scope_gate_configs.sql", "`"),
    ("db-prod-pg/V93-add_knowledge_scope_gate_configs.sql", '"'),
)

# 值一律是单引号字符串字面量，与标识符引用符无关
SEEDED = (
    ("knowledge_scope_gate_enabled", "'true'"),
    ("knowledge_scope_gate_threshold", "'5'"),
    ("knowledge_scope_gate_ranked_limit", "'3'"),
)


@pytest.mark.parametrize("relative_path,quote", MIGRATIONS)
def test_migration_seeds_all_scope_gate_configs(relative_path, quote):
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "system_configs" in source
    assert f"{quote}key{quote}" in source
    for key, default in SEEDED:
        assert f"'{key}'" in source, f"{relative_path} 缺少 {key}"
        assert default in source, f"{relative_path} 缺少 {key} 的默认值 {default}"
    assert "'knowledge'" in source


@pytest.mark.parametrize("relative_path,_quote", MIGRATIONS)
def test_migration_never_overwrites_admin_tuned_values(relative_path, _quote):
    """冲突时应只刷描述与分类；若把 value 也刷回去，管理员调过的阈值会被升级冲掉。"""
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "VALUES(`value`)" not in source
    assert 'EXCLUDED."value"' not in source
    assert 'VALUES(`description`)' in source or 'EXCLUDED."description"' in source
