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
