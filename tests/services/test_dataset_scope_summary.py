"""数据集目录的「权限摘要」：`list_accessible_datasets` 返回什么、为什么这么给。

摘要只回答三件模型真的会用来做决策的事：**能看几张表、字段有没有被裁、这次查询
到底查不查得到数据**，外加到期时间。刻意**不**返回的东西：行级条件原文
（`dept_code='FIN'` 属于授权规则细节）、被隐藏的列名、完整表结构。
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.dataset_scope_service import (
    MODE_ALL,
    MODE_LIST,
    ROW_POLICY_CUSTOM,
    ROW_POLICY_INHERIT,
    ROW_POLICY_NONE,
    ROW_STATE_CUSTOM,
    ROW_STATE_DENY_ALL,
    ROW_STATE_FILTERED,
    ROW_STATE_NONE,
    ColumnRule,
    DatasetScope,
    describe_row_state,
    summarize_dataset_scopes,
)

pytestmark = pytest.mark.no_infrastructure


def _scope(**kwargs) -> DatasetScope:
    base = {"dataset_id": 1, "mode": MODE_ALL, "table_ids": frozenset()}
    base.update(kwargs)
    return DatasetScope(**base)


# ---------------------------------------------------------------------------
# 行级效果档位
# ---------------------------------------------------------------------------


def test_custom_row_policy_reports_custom():
    scope = _scope(row_policy=ROW_POLICY_CUSTOM, row_condition="dept_code = 'FIN'")
    assert describe_row_state(scope) == ROW_STATE_CUSTOM


def test_none_row_policy_reports_none():
    assert describe_row_state(_scope(row_policy=ROW_POLICY_NONE)) == ROW_STATE_NONE


def test_inherit_without_data_perm_has_no_row_restriction():
    """数据集没开数据权限 → 继承等于没有行级限制，摘要里不该出现 row。"""
    assert describe_row_state(_scope(row_policy=ROW_POLICY_INHERIT), enable_data_perm=False) is None


def test_inherit_with_empty_policy_is_deny_all():
    """开了数据权限但一层规则都没配 → 运行期 1=0，必须在目录里就说清楚。"""
    state = describe_row_state(
        _scope(row_policy=ROW_POLICY_INHERIT),
        enable_data_perm=True,
        row_filter_config={"user_overrides": {}, "role_policies": {}, "default_policy": []},
        user_roles=["viewer"],
        user_id=7,
    )
    assert state == ROW_STATE_DENY_ALL


def test_inherit_with_matching_role_rule_is_filtered():
    state = describe_row_state(
        _scope(row_policy=ROW_POLICY_INHERIT),
        enable_data_perm=True,
        row_filter_config={"role_policies": {"viewer": [{"condition": "dept_code = 'FIN'"}]}},
        user_roles=["viewer"],
        user_id=7,
    )
    assert state == ROW_STATE_FILTERED


def test_inherit_with_explicit_allow_all_rule_is_not_deny():
    state = describe_row_state(
        _scope(row_policy=ROW_POLICY_INHERIT),
        enable_data_perm=True,
        row_filter_config={"default_policy": [{"condition": "1=1"}]},
        user_roles=[],
        user_id=7,
    )
    assert state == ROW_STATE_FILTERED


# ---------------------------------------------------------------------------
# 摘要结构
# ---------------------------------------------------------------------------


def _fake_session(count_rows, name_rows=()):
    db = AsyncMock()
    count_result = MagicMock()
    count_result.all = MagicMock(return_value=list(count_rows))
    name_result = MagicMock()
    name_result.all = MagicMock(return_value=list(name_rows))
    db.execute = AsyncMock(side_effect=[count_result, name_result])
    return db


@pytest.mark.asyncio
async def test_summary_reports_table_scope_and_column_scope():
    db = _fake_session([(1, 31)], [(92, "ai_agent_access_logs")])
    scopes = {
        1: _scope(
            mode=MODE_LIST,
            table_ids=frozenset({92}),
            sources=("user",),
            column_rules={92: (ColumnRule("exclude", {"query_params", "ip"}),)},
            expires_at=datetime(2026, 12, 31, 23, 59),
        )
    }

    out = await summarize_dataset_scopes(db, scopes, dataset_rows=[], user_roles=[], user_id=7)

    summary = out[1]
    assert summary["tables"] == {
        "mode": MODE_LIST,
        "visible": 1,
        "total": 31,
        "names": ["ai_agent_access_logs"],
    }
    assert summary["columns"] == {
        "restricted_tables": 1,
        "excluded": 2,
        "whitelisted_tables": 0,
    }
    assert summary["sources"] == ["user"]
    assert summary["expires_at"] == "2026-12-31 23:59"
    # 行级条件原文绝不出口
    assert "row_condition" not in summary
    assert "dept_code" not in str(summary)


@pytest.mark.asyncio
async def test_unrestricted_dataset_is_omitted():
    """mode=all + 无字段/行级限制 + 无到期 → 不输出 permission，省 token。"""
    db = _fake_session([(9, 5)])
    scopes = {9: _scope(dataset_id=9, sources=("all",))}

    out = await summarize_dataset_scopes(db, scopes, dataset_rows=[], user_roles=[], user_id=7)

    assert out == {}


@pytest.mark.asyncio
async def test_row_deny_all_appears_in_summary():
    row = MagicMock(id=1, enable_data_perm=True, row_filter_config={"default_policy": []})
    db = _fake_session([(1, 3)])
    scopes = {1: _scope(row_policy=ROW_POLICY_INHERIT)}

    out = await summarize_dataset_scopes(
        db, scopes, dataset_rows=[row], user_roles=["viewer"], user_id=7
    )

    assert out[1]["row"] == ROW_STATE_DENY_ALL


@pytest.mark.asyncio
async def test_table_names_omitted_when_too_many():
    """可见表超过 name_limit 时只给计数，不把几十张表名塞进上下文。"""
    table_ids = list(range(100, 115))
    name_rows = [(tid, f"t_{tid}") for tid in table_ids]
    db = _fake_session([(1, 40)], name_rows)
    scopes = {1: _scope(mode=MODE_LIST, table_ids=frozenset(table_ids))}

    out = await summarize_dataset_scopes(
        db, scopes, dataset_rows=[], user_roles=[], user_id=7, name_limit=8
    )

    assert out[1]["tables"]["names"] is None
    assert out[1]["tables"]["visible"] == 15
    assert out[1]["tables"]["total"] == 40


@pytest.mark.asyncio
async def test_include_mode_reports_whitelist_not_excluded():
    db = _fake_session([(1, 10)])
    scopes = {
        1: _scope(mode=MODE_LIST, table_ids=frozenset({5}), column_rules={5: (ColumnRule("include", {"a", "b"}),)})
    }

    out = await summarize_dataset_scopes(db, scopes, dataset_rows=[], user_roles=[], user_id=7)

    assert out[1]["columns"] == {
        "restricted_tables": 1,
        "excluded": 0,
        "whitelisted_tables": 1,
    }


@pytest.mark.asyncio
async def test_summary_survives_count_query_failure():
    """统计表数失败只让 total 归零，不能反过来让目录报错。"""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("boom"))
    scopes = {1: _scope(mode=MODE_LIST, table_ids=frozenset({92}))}

    out = await summarize_dataset_scopes(db, scopes, dataset_rows=[], user_roles=[], user_id=7)

    assert out[1]["tables"]["visible"] == 1
    assert out[1]["tables"]["total"] == 0


@pytest.mark.asyncio
async def test_empty_scopes_returns_empty_map():
    assert await summarize_dataset_scopes(AsyncMock(), {}, dataset_rows=[], user_roles=[], user_id=7) == {}
