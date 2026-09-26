"""会话历史查询构建：来源过滤与轮次级存在性语义。"""

import re
from datetime import datetime

import pytest
from sqlalchemy import select

from app.services.ai.history_query import (
    build_history_query,
    normalize_scope,
    scope_condition,
    turn_level_conversation_keys,
)

pytestmark = pytest.mark.no_infrastructure


def _compile(stmt) -> str:
    """编译为单行 SQL 文本，便于断言结构。"""
    raw = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    return " ".join(raw.split())


def _has_turn_filter_subquery(sql: str) -> bool:
    """判断 SQL 中是否出现轮次筛选子查询。

    两个坑：轮次子查询带 ``DISTINCT``（实际是 ``IN (SELECT DISTINCT ...``）；
    而分组查询的 ``JOIN (SELECT ...)`` 里也含有 ``IN (`` 子串。
    因此必须加词边界 ``\\b``，不能用 ``"IN (SELECT"`` 字面量。
    """
    return re.search(r"\bIN\s*\(\s*SELECT", sql, re.IGNORECASE) is not None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("task", "task"),
        ("TASK", "task"),
        (" chat ", "chat"),
        ("all", "all"),
        (None, "all"),
        ("", "all"),
        ("bogus", "all"),
    ],
)
def test_normalize_scope_falls_back_to_all(raw, expected):
    assert normalize_scope(raw) == expected


def test_scope_condition_task_matches_prefix():
    assert "task_conv_%" in _compile(select(scope_condition("task")))


def test_scope_condition_chat_includes_null_and_excludes_task():
    compiled = _compile(select(scope_condition("chat")))
    assert "IS NULL" in compiled.upper()
    assert "NOT LIKE" in compiled.upper()
    assert "task_conv_%" in compiled


def test_scope_condition_all_returns_none():
    assert scope_condition("all") is None


def test_scope_condition_invalid_value_returns_none():
    assert scope_condition("nonsense") is None


def test_turn_level_keys_merge_keyword_and_status_into_one_subquery():
    stmt = turn_level_conversation_keys(user_id="7", keyword="巡检", status="failed")
    compiled = _compile(stmt)
    # 关键词与状态必须同处一个子查询：只应存在一个 SELECT
    assert compiled.upper().count("SELECT") == 1
    assert "巡检" in compiled
    assert "failed" in compiled
    assert "user_id = '7'" in compiled


def test_grouped_keyword_uses_conversation_key_subquery():
    query, _ = build_history_query(
        user_id="7", keyword="巡检", group_by_conversation=True
    )
    assert _has_turn_filter_subquery(_compile(query))


def test_grouped_status_uses_conversation_key_subquery():
    query, _ = build_history_query(
        user_id="7", status="failed", group_by_conversation=True
    )
    assert _has_turn_filter_subquery(_compile(query))


def test_ungrouped_keyword_filters_current_row_only():
    query, _ = build_history_query(
        user_id="7", keyword="巡检", group_by_conversation=False
    )
    compiled = _compile(query)
    assert not _has_turn_filter_subquery(compiled)
    assert "巡检" in compiled


def test_grouped_query_without_turn_filters_has_no_subquery_filter():
    query, _ = build_history_query(user_id="7", group_by_conversation=True)
    assert not _has_turn_filter_subquery(_compile(query))


def test_time_range_filters_representative_row():
    query, _ = build_history_query(
        user_id="7",
        start_dt=datetime(2026, 9, 1),
        group_by_conversation=True,
    )
    assert "created_at >=" in _compile(query)


def test_count_query_is_built_before_pagination():
    query, count = build_history_query(user_id="7", page=3, page_size=20)
    assert "LIMIT" in _compile(query).upper()
    assert "LIMIT" not in _compile(count).upper()


def test_scope_filter_is_applied_in_grouped_mode():
    query, _ = build_history_query(
        user_id="7", scope="task", group_by_conversation=True
    )
    assert "task_conv_%" in _compile(query)


def test_username_scope_used_when_user_id_absent():
    query, _ = build_history_query(username="alice", group_by_conversation=False)
    assert "username = 'alice'" in _compile(query)
