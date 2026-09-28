"""大模型请求读取超时配置的迁移契约。

这里固化两件容易写错、且错了不会被其它测试发现的事：
1. 分组必须是 'agent'——V68 已废弃独立的 llm 分组，llm_model_name /
   llm_temperature 都在「智能体设置」组里，新配置项必须跟着走；
2. 默认值必须是 180——它由 producer 看门狗（600s）与流式重试预算（2 次）
   共同推导得出，随手改动会让重试来不及执行。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]

MYSQL_MIGRATION = ROOT / "db-prod/V160-add-llm-request-read-timeout.sql"
PG_MIGRATION = ROOT / "db-prod-pg/V61-add-llm-request-read-timeout.sql"


def _sql_only(path: Path) -> str:
    """剔除 `--` 注释行：注释里的说明性字样不应参与结构断言。"""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )


def test_mysql_migration_seeds_config_in_agent_group():
    sql = _sql_only(MYSQL_MIGRATION)

    assert "llm_request_read_timeout" in sql
    assert "'180'" in sql
    assert "'agent'" in sql
    # 独立的 llm 分组已废弃，防止再次写回
    assert "'llm'" not in sql
    # 可重跑，但只纠正分组与描述，不覆盖运维调过的 value
    assert "ON DUPLICATE KEY UPDATE" in sql.upper()
    update_clause = sql.upper().split("ON DUPLICATE KEY UPDATE", 1)[1]
    assert "`VALUE`" not in update_clause


def test_postgres_migration_seeds_config_in_agent_group():
    sql = _sql_only(PG_MIGRATION)

    assert "llm_request_read_timeout" in sql
    assert "'180'" in sql
    assert "'agent'" in sql
    assert "'llm'" not in sql
    assert "ON CONFLICT" in sql.upper()
    assert "FALSE" in sql
    update_clause = sql.upper().split("ON CONFLICT", 1)[1]
    assert '"VALUE"' not in update_clause


def test_migrations_document_the_watchdog_derived_default():
    """默认值 180 的推导依据必须留在迁移里，避免被当成随手填的数字。"""
    for migration in (MYSQL_MIGRATION, PG_MIGRATION):
        source = migration.read_text(encoding="utf-8")
        assert "600" in source
        assert "180" in source
