"""SQL 门禁加固回归：UNION/子查询别名/系统库/版本注释/行锁/危险函数。"""

import json
from unittest.mock import AsyncMock

import pytest

from app.services.ai.sql_sandbox_gate import SQLSandboxGate
from app.services.ai.tools.data_api import _clamp_select_row_limit, validate_sql
from app.services.sql_query_execution_service import (
    execute_sql_query_core,
    extract_physical_table_refs_from_select_sql,
)


def _refs(sql: str) -> tuple[str | None, dict[str, str]]:
    return extract_physical_table_refs_from_select_sql(sql, "mysql")


def test_union_branches_are_all_collected() -> None:
    error, refs = _refs("SELECT * FROM t1 UNION ALL SELECT * FROM t2")

    assert error is None
    assert set(refs) == {"t1", "t2"}


def test_subquery_alias_does_not_hide_physical_table() -> None:
    error, refs = _refs("SELECT * FROM (SELECT * FROM secret_t) secret_t")

    assert error is None
    assert set(refs) == {"secret_t"}


def test_cte_alias_is_not_treated_as_physical_table() -> None:
    error, refs = _refs("WITH cte AS (SELECT * FROM t1) SELECT * FROM cte")

    assert error is None
    assert set(refs) == {"t1"}


def test_system_schema_is_rejected_instead_of_exempted() -> None:
    error, refs = _refs("SELECT * FROM mysql.user")

    assert "System schema" in str(error)
    assert refs == {}


def test_version_comment_is_rejected_by_both_gates() -> None:
    sql = "SELECT 1 /*!50000 UNION ALL SELECT * FROM mysql.user */"

    assert "version comments" in str(validate_sql(sql, dialect="mysql"))
    assert "version comments" in str(_refs(sql)[0])


def test_locking_read_is_rejected() -> None:
    assert "Locking clauses" in str(validate_sql("SELECT * FROM t1 FOR UPDATE", dialect="mysql"))
    assert "Locking clauses" in str(
        validate_sql("SELECT * FROM t1 LOCK IN SHARE MODE", dialect="mysql")
    )


def test_dangerous_function_is_rejected() -> None:
    assert "Dangerous function" in str(validate_sql("SELECT SLEEP(60)", dialect="mysql"))


def test_describe_and_explain_keep_table_refs() -> None:
    for sql in ("DESCRIBE secret_t", "EXPLAIN SELECT * FROM secret_t"):
        error, refs = _refs(sql)
        assert error is None
        assert set(refs) == {"secret_t"}


def test_remote_limit_is_clamped_in_ast() -> None:
    assert _clamp_select_row_limit(
        "SELECT * FROM t LIMIT 5000", dialect="mysql", max_rows=1000
    ).endswith("LIMIT 1000")
    assert (
        _clamp_select_row_limit("SELECT * FROM t LIMIT 10", dialect="mysql", max_rows=1000)
        == "SELECT * FROM t LIMIT 10"
    )
    # 子查询里的 LIMIT 不能顶掉外层上限。
    assert _clamp_select_row_limit(
        "SELECT * FROM (SELECT * FROM t LIMIT 99999) x", dialect="mysql", max_rows=1000
    ).endswith("LIMIT 1000")


@pytest.mark.asyncio
async def test_auth_check_only_does_not_call_sandbox_or_external_api(monkeypatch) -> None:
    called = False

    async def fake_verify(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("auth_check_only must not EXPLAIN externally")

    monkeypatch.setattr(SQLSandboxGate, "verify_and_optimize", fake_verify)

    result = await execute_sql_query_core(
        AsyncMock(),
        sql="SELECT 1",
        data_source="mysql_test",
        auth_check_only=True,
        dry_run=False,
    )

    assert json.loads(result) == {"allowed": True}
    assert called is False
