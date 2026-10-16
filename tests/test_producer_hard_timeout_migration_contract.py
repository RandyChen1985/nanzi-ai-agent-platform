"""producer 硬超时看门狗的迁移契约。

这里固化三件容易写错、且错了不会被其它测试发现的事：
1. 分组必须是 'agent'——它和 agent_max_toolcall_timeout 是「整轮」与「单个工具」的
   关系，必须在同一组里并排可见，才看得出冲突；
2. 描述必须写明冲突方向：工具超时配得比看门狗大不会生效。运维看到的值大就以为能跑
   更久，实际整轮会先被取消；
3. 默认值必须是 600，且描述里要交代 0 表示关闭——否则没人知道这个开关能关。
"""

import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]

MYSQL_MIGRATION = ROOT / "db-prod/V172-add-producer-hard-timeout-config.sql"
PG_MIGRATION = ROOT / "db-prod-pg/V73-add-producer-hard-timeout-config.sql"

CONFIG_KEY = "agent_chat_producer_hard_timeout_seconds"


def _sql_only(path: Path) -> str:
    """剔除 `--` 注释行：注释里的说明性字样不应参与结构断言。"""
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )


def test_mysql_migration_seeds_config_in_agent_group():
    sql = _sql_only(MYSQL_MIGRATION)

    assert CONFIG_KEY in sql
    assert "'600'" in sql
    assert "'agent'" in sql
    # 可重跑，但只纠正分组与描述，不覆盖运维调过的 value
    assert "ON DUPLICATE KEY UPDATE" in sql.upper()
    update_clause = sql.upper().split("ON DUPLICATE KEY UPDATE", 1)[1]
    assert "`VALUE`" not in update_clause


def test_postgres_migration_seeds_config_in_agent_group():
    sql = _sql_only(PG_MIGRATION)

    assert CONFIG_KEY in sql
    assert "'600'" in sql
    assert "'agent'" in sql
    assert "ON CONFLICT" in sql.upper()
    assert "FALSE" in sql
    update_clause = sql.upper().split("ON CONFLICT", 1)[1]
    assert '"VALUE"' not in update_clause


@pytest.mark.parametrize("migration", [MYSQL_MIGRATION, PG_MIGRATION])
def test_description_documents_the_tool_timeout_conflict(migration):
    """描述必须点明「整轮 vs 单个工具」，否则这个坑会一直有人踩。"""
    source = migration.read_text(encoding="utf-8")

    assert "agent_max_toolcall_timeout" in source
    assert "整轮" in source
    # 0 = 关闭的语义要交代，否则没人知道这个开关能关
    assert "0 表示关闭" in source
    # 范围要与后端校验一致
    assert "60" in source and "3600" in source


@pytest.mark.parametrize("migration", [MYSQL_MIGRATION, PG_MIGRATION])
def test_description_mentions_releasing_the_session_lock(migration):
    """看门狗的真正职责是释放会话锁，迁移里要说清，避免被当成「让任务更快结束」。"""
    assert "会话锁" in migration.read_text(encoding="utf-8")


def _numeric_versions(directory: Path) -> list[int]:
    """取目录下 V<整数>-*.sql 的编号；历史小数版本（如 V3.1）不参与比较。"""
    versions: list[int] = []
    for path in directory.glob("V*.sql"):
        match = re.match(r"V(\d+)-", path.name)
        if match:
            versions.append(int(match.group(1)))
    return sorted(versions)


def test_migration_versions_do_not_collide():
    """两套迁移各自独立编号，写重了 apply 脚本会静默跳过其中一个。"""
    assert MYSQL_MIGRATION.exists() and PG_MIGRATION.exists()
    assert _numeric_versions(ROOT / "db-prod")[-1] == 172
    assert _numeric_versions(ROOT / "db-prod-pg")[-1] == 73
