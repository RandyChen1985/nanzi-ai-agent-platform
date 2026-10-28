"""两套迁移必须同结构：漏一个列或索引，会在只跑其中一套的部署里变成运行时 500。

除了「两个文件的列集合相等」，这里还钉住三件容易漏掉的事：

1. 列名白名单——两个文件同时漏掉某一列时，单纯的互相比对仍然是绿的；
2. 类型白名单覆盖——类型正则只认固定几种类型，若某列写成 ``BOOLEAN``/``JSON``，
   它会对比较「隐身」（两边都看不见，集合依然相等）；
3. 索引的真实定义——只做子串包含判断的话，索引名只出现在注释里也能通过，而且
   同名索引的列不同（MySQL 单列、PG 复合列）也测不出来。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.no_infrastructure

MYSQL_SQL = "db-prod/V197-create-conversation-shares.sql"
PG_SQL = "db-prod-pg/V97-create-conversation-shares.sql"

MYSQL_COLUMN_RE = re.compile(
    r"^\s*`([a-z_]+)`\s+(BIGINT|VARCHAR|INT|DATETIME|LONGTEXT)", re.MULTILINE
)
PG_COLUMN_RE = re.compile(
    r"^\s{2}([a-z_]+)\s+(BIGSERIAL|VARCHAR|INTEGER|TIMESTAMP|TEXT)", re.MULTILINE
)
INDEX_NAMES = (
    "uk_ai_conversation_shares_share_id",
    "idx_ai_conversation_shares_owner",
    "idx_ai_conversation_shares_conversation",
    "idx_ai_conversation_shares_created_at",
)
# 列的权威清单：与 ORM 模型、两套迁移三方对齐，防止「两边一起漏」蒙过集合比对。
EXPECTED_COLUMNS = {
    "id",
    "share_id",
    "conversation_id",
    "owner_user_id",
    "owner_username",
    "agent_id",
    "agent_name",
    "title",
    "access_mode",
    "secret_cipher",
    "content_json",
    "message_count",
    "turn_count",
    "expires_at",
    "status",
    "revoked_at",
    "view_count",
    "last_viewed_at",
    "created_at",
}

# 不限定类型的「任意列」匹配，用来发现被类型正则漏掉的列。
MYSQL_ANY_COLUMN_RE = re.compile(r"^\s*`([a-z_][a-z0-9_]*)`\s+([A-Z][A-Z0-9]*)", re.MULTILINE)
PG_ANY_COLUMN_RE = re.compile(r"^\s{2}([a-z_][a-z0-9_]*)\s+([A-Z][A-Z0-9]*)", re.MULTILINE)

MYSQL_INDEX_RE = re.compile(
    r"^\s*(?:UNIQUE\s+)?KEY\s+`([a-z0-9_]+)`\s*\(([^)]*)\)", re.MULTILINE
)
PG_INDEX_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z0-9_]+)\s+"
    r"ON\s+[a-z0-9_]+\s*\(([^)]*)\)",
    re.IGNORECASE,
)


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _create_table_body(sql: str) -> str:
    """截取 CREATE TABLE 括号内的定义体。

    只看这一段，``ON ai_conversation_shares (...)`` 这类建索引续行才不会
    被误当成列定义。
    """
    match = re.search(r"CREATE TABLE[^(]*\((.*?)\n\)", sql, re.IGNORECASE | re.DOTALL)
    assert match, "迁移脚本缺少 CREATE TABLE 定义"
    return match.group(1)


def _normalize_columns(raw: str) -> tuple:
    """把 ``(`a`, `b`)`` 或 ``(a, b)`` 归一成 ('a', 'b')，便于两库对比。"""
    return tuple(part.strip().strip("`") for part in raw.split(",") if part.strip())


def test_both_migrations_exist():
    assert (ROOT / MYSQL_SQL).exists()
    assert (ROOT / PG_SQL).exists()


def test_column_sets_are_identical():
    mysql_columns = {name for name, _type in MYSQL_COLUMN_RE.findall(_source(MYSQL_SQL))}
    pg_columns = {name for name, _type in PG_COLUMN_RE.findall(_source(PG_SQL))}
    assert mysql_columns == pg_columns, mysql_columns ^ pg_columns
    assert "content_json" in mysql_columns
    assert "secret_cipher" in mysql_columns
    # 两个文件「一起漏」也必须红：结果要和权威清单完全一致。
    assert mysql_columns == EXPECTED_COLUMNS, mysql_columns ^ EXPECTED_COLUMNS
    assert pg_columns == EXPECTED_COLUMNS, pg_columns ^ EXPECTED_COLUMNS


def test_no_column_escapes_the_type_whitelist():
    """类型白名单之外的列会对比较隐身，这里显式把它抓出来。"""
    mysql_body = _create_table_body(_source(MYSQL_SQL))
    mysql_any = set(MYSQL_ANY_COLUMN_RE.findall(mysql_body))
    mysql_known = set(MYSQL_COLUMN_RE.findall(mysql_body))
    assert mysql_any == mysql_known, (
        "MySQL 这些列的类型不在一致性比较的类型白名单内，会被漏比：" f"{mysql_any - mysql_known}"
    )

    pg_body = _create_table_body(_source(PG_SQL))
    pg_any = set(PG_ANY_COLUMN_RE.findall(pg_body))
    pg_known = set(PG_COLUMN_RE.findall(pg_body))
    assert pg_any == pg_known, (
        "PG 这些列的类型不在一致性比较的类型白名单内，会被漏比：" f"{pg_any - pg_known}"
    )


def test_index_names_match():
    mysql_sql = _source(MYSQL_SQL)
    pg_sql = _source(PG_SQL)
    for name in INDEX_NAMES:
        assert name in mysql_sql, f"MySQL 迁移缺索引 {name}"
        assert name in pg_sql, f"PG 迁移缺索引 {name}"

    mysql_indexes = {
        name: _normalize_columns(columns)
        for name, columns in MYSQL_INDEX_RE.findall(_create_table_body(mysql_sql))
    }
    pg_indexes = {
        name: _normalize_columns(columns) for name, columns in PG_INDEX_RE.findall(pg_sql)
    }
    # 名字相同还不够：同名索引落在哪些列、是否唯一，两边必须一致。
    assert set(mysql_indexes) == set(INDEX_NAMES), set(mysql_indexes) ^ set(INDEX_NAMES)
    assert set(pg_indexes) == set(INDEX_NAMES), set(pg_indexes) ^ set(INDEX_NAMES)
    assert mysql_indexes == pg_indexes, mysql_indexes


def test_primary_key_is_id_on_both_dialects():
    mysql_body = _create_table_body(_source(MYSQL_SQL))
    assert re.search(r"^\s*PRIMARY\s+KEY\s*\(`id`\)", mysql_body, re.MULTILINE)
    assert re.search(r"^\s{2}id\s+BIGSERIAL\s+PRIMARY\s+KEY", _source(PG_SQL), re.MULTILINE)
