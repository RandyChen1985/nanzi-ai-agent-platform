"""分享与系统级发布迁移的契约。

两套库的编号体系不同（MySQL V185/V186、PG V85/V86），不能混用；
这里守的是「文件存在、编号对得上、建表语句里的状态枚举与代码一致」，
避免改了代码忘了改注释，或者把 PG 的编号写进 MySQL 目录。
"""
from pathlib import Path

import pytest


MYSQL_PUBLISH = Path("db-prod/V185-create_agent_system_publication.sql")
MYSQL_SHARE = Path("db-prod/V186-create_agent_share_requests.sql")
PG_PUBLISH = Path("db-prod-pg/V85-create_agent_system_publication.sql")
PG_SHARE = Path("db-prod-pg/V86-create_agent_share_requests.sql")

ALL_SHARE_STATUSES = ("PENDING", "ACCEPTED", "REJECTED", "EXPIRED", "REVOKED", "WITHDRAWN")
ALL_EVENT_ACTIONS = (
    "PUBLISH",
    "UNPUBLISH",
    "BASELINE_PUBLISH",
    "TRANSFER_OWNER",
    "SHARE_SEND",
    "SHARE_ACCEPT",
    "SHARE_REJECT",
    "SHARE_REVOKE",
)


@pytest.mark.parametrize("path", [MYSQL_PUBLISH, MYSQL_SHARE, PG_PUBLISH, PG_SHARE])
def test_migration_file_exists(path: Path):
    assert path.exists(), f"缺少迁移文件：{path}"


@pytest.mark.parametrize("path", [MYSQL_SHARE, PG_SHARE])
def test_share_status_comment_lists_every_status(path: Path):
    """状态枚举漏一个，下一个人就会以为 WITHDRAWN 是不该存在的值。"""
    content = path.read_text(encoding="utf-8")
    for status in ALL_SHARE_STATUSES:
        assert status in content, f"{path} 的注释缺少状态 {status}"


@pytest.mark.parametrize("path", [MYSQL_PUBLISH, PG_PUBLISH])
def test_event_action_comment_lists_share_actions(path: Path):
    """分享事件写进同一张事件表，注释里不列出来就会被当成非法值。"""
    content = path.read_text(encoding="utf-8")
    for action in ALL_EVENT_ACTIONS:
        assert action in content, f"{path} 的事件注释缺少 {action}"


def test_mysql_share_uses_bigint_user_ids():
    """用户表主键是 BIGINT，分享表用 INT 迟早溢出。"""
    content = MYSQL_SHARE.read_text(encoding="utf-8")
    assert "owner_user_id` BIGINT" in content
    assert "recipient_user_id` BIGINT" in content
    assert "owner_user_id` INT " not in content


def test_migrations_do_not_declare_physical_foreign_keys():
    """项目惯例：不用物理外键，清理统一由应用层负责。"""
    for path in (MYSQL_SHARE, PG_SHARE, MYSQL_PUBLISH, PG_PUBLISH):
        content = path.read_text(encoding="utf-8")
        # 只看建表语句本身；注释里出现「外键」说明是刻意说明为何不加
        ddl = "\n".join(
            line for line in content.splitlines() if not line.strip().startswith("--")
        ).upper()
        assert "FOREIGN KEY" not in ddl, f"{path} 不应有物理外键"
        assert "REFERENCES" not in ddl, f"{path} 不应有 REFERENCES"


def test_action_column_length_matches_across_databases():
    """MySQL、PostgreSQL、ORM 三处的 action 长度必须一致。

    改了一个漏另外两个，就会出现「MySQL 存得下、PG 截断」这种只在某一套
    数据库上复现的问题。
    """
    import re

    mysql_ddl = MYSQL_PUBLISH.read_text(encoding="utf-8")
    pg_ddl = PG_PUBLISH.read_text(encoding="utf-8")
    model = Path("app/models/agent_system.py").read_text(encoding="utf-8")

    mysql_len = re.search(r"`action` VARCHAR\((\d+)\)", mysql_ddl)
    pg_len = re.search(r"\baction VARCHAR\((\d+)\)", pg_ddl)
    orm_len = re.search(r"action = Column\(String\((\d+)\)", model)

    assert mysql_len and pg_len and orm_len, "三处都应能找到 action 的长度定义"
    lengths = {mysql_len.group(1), pg_len.group(1), orm_len.group(1)}
    assert len(lengths) == 1, f"action 长度不一致：MySQL/PG/ORM = {lengths}"


def test_share_user_id_type_matches_across_databases():
    """两套库的用户 ID 列都必须是 64 位整数，且与 ai_agent_users 主键一致。"""
    mysql_ddl = MYSQL_SHARE.read_text(encoding="utf-8").lower()
    pg_ddl = PG_SHARE.read_text(encoding="utf-8").lower()

    for column in ("owner_user_id", "recipient_user_id"):
        assert f"{column}` bigint" in mysql_ddl, f"MySQL 的 {column} 应为 BIGINT"
        assert f"{column} bigint" in pg_ddl, f"PG 的 {column} 应为 bigint"
