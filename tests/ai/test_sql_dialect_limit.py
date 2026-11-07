"""SQL 方言行数限制 helper 单元测试。"""

import pytest

from app.services.ai.sql_dialect_limit import apply_dialect_row_limit, dialect_limit_hint

pytestmark = pytest.mark.no_infrastructure


def test_oracle_wraps_inner_select_with_rownum():
    sql = apply_dialect_row_limit(
        "SELECT CREATE_DATE FROM VIEW_AI_CULEOPP WHERE CREATE_DATE IS NOT NULL",
        dialect="oracle",
        limit=5,
    )
    assert "ROWNUM <= 5" in sql.upper()
    assert "SELECT CREATE_DATE FROM VIEW_AI_CULEOPP" in sql


def test_mysql_uses_limit():
    sql = apply_dialect_row_limit(
        "SELECT CREATE_DATE FROM t WHERE CREATE_DATE IS NOT NULL",
        dialect="mysql",
        limit=5,
    )
    assert sql.endswith("LIMIT 5")


def test_clickhouse_uses_limit():
    sql = apply_dialect_row_limit(
        "SELECT DISTINCT gxqy FROM zf_view_resroom",
        dialect="clickhouse",
        limit=20,
    )
    assert "LIMIT 20" in sql


def test_sqlserver_uses_top():
    sql = apply_dialect_row_limit(
        "SELECT id FROM hrmresource",
        dialect="sqlserver",
        limit=3,
    )
    assert "SELECT TOP 3" in sql.upper()


def test_sqlserver_top_handles_order_by_without_wrapping_subquery():
    sql = apply_dialect_row_limit(
        "SELECT bm, SUM(amount) AS total_amount FROM t_cw_clg GROUP BY bm ORDER BY total_amount DESC",
        dialect="tsql",
        limit=1000,
        max_limit=1000,
    )

    assert "SELECT TOP 1000" in sql.upper()
    assert "ORDER BY TOTAL_AMOUNT DESC" in sql.upper()
    assert "LIMIT" not in sql.upper()
    assert "FROM (" not in sql.upper()


def test_sqlserver_existing_top_is_clamped():
    sql = apply_dialect_row_limit(
        "SELECT TOP 5000 id FROM hrmresource ORDER BY id",
        dialect="mssql",
        limit=1000,
        max_limit=1000,
    )

    assert "TOP 1000" in sql.upper()
    assert "TOP 5000" not in sql.upper()


def test_sqlserver_cte_uses_outer_top():
    sql = apply_dialect_row_limit(
        "WITH x AS (SELECT bm, amount FROM t_cw_clg) SELECT bm, SUM(amount) AS total_amount FROM x GROUP BY bm ORDER BY total_amount DESC",
        dialect="sqlserver",
        limit=1000,
        max_limit=1000,
    )

    assert "WITH X AS" in sql.upper()
    assert "SELECT TOP 1000 BM" in sql.upper()
    assert "LIMIT" not in sql.upper()


def test_dialect_limit_hint():
    assert "ROWNUM" in dialect_limit_hint("oracle_ds")
    assert "TOP" in dialect_limit_hint("mssql_prod")
    assert "LIMIT" in dialect_limit_hint("clickhouse_default")


# --- 回归：外层封顶不能被内层 LIMIT / 末尾注释绕过 -------------------------


def test_inner_subquery_limit_does_not_disable_outer_limit():
    """子查询里的 LIMIT 不是外层上限。

    旧实现用 `" LIMIT " in sql.upper()` 判断，内层 `LIMIT 10` 会让外层整条跳过
    封顶，结果 AI 分析实际拉到全表。
    """
    sql = apply_dialect_row_limit(
        "SELECT * FROM (SELECT id FROM t LIMIT 10) x",
        dialect="mysql",
        limit=1000,
        max_limit=1000,
    )
    assert sql.count("LIMIT") == 2
    assert sql.rstrip().endswith("LIMIT 1000")


def test_limit_keyword_inside_string_literal_is_ignored():
    sql = apply_dialect_row_limit(
        "SELECT * FROM t WHERE name = 'a LIMIT b'",
        dialect="clickhouse",
        limit=20,
    )
    assert sql.rstrip().endswith("LIMIT 20")


def test_trailing_line_comment_does_not_swallow_limit():
    sql = apply_dialect_row_limit(
        "SELECT id FROM t -- 只看这一行",
        dialect="mysql",
        limit=1000,
        max_limit=1000,
    )
    assert "--" not in sql
    assert sql.rstrip().endswith("LIMIT 1000")


def test_trailing_comment_and_semicolon_are_stripped_before_oracle_wrap():
    """Oracle 包装的 `)` 不能被末尾 `--` 注释吞掉（否则直接语法错误）。"""
    sql = apply_dialect_row_limit(
        "SELECT id FROM t; -- 尾注",
        dialect="oracle",
        limit=5,
    )
    assert ";" not in sql
    assert sql.rstrip().endswith("WHERE ROWNUM <= 5")


def test_oversized_outer_limit_is_clamped():
    sql = apply_dialect_row_limit(
        "SELECT id FROM t LIMIT 99999",
        dialect="mysql",
        limit=1000,
        max_limit=1000,
    )
    assert sql.rstrip().endswith("LIMIT 1000")


def test_sqlserver_inner_top_does_not_disable_outer_top():
    sql = apply_dialect_row_limit(
        "SELECT * FROM (SELECT TOP 5 id FROM t) x",
        dialect="sqlserver",
        limit=1000,
        max_limit=1000,
    )
    assert "SELECT TOP 1000" in sql.upper()

