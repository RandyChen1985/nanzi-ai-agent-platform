"""SQL AST 表名提取（元数据权限前置逻辑）单测。"""

import pytest

from app.services.sql_query_execution_service import extract_physical_table_refs_from_select_sql

pytestmark = pytest.mark.no_infrastructure


def test_extract_tables_simple_join():
    err, refs = extract_physical_table_refs_from_select_sql(
        "SELECT * FROM a.Bb JOIN cc AS z ON 1", dialect="mysql"
    )
    assert err is None
    assert refs["bb"] == "Bb"
    assert refs["cc"] == "cc"


def test_extract_skips_cte_and_subquery_alias():
    sql = """
    WITH t AS (SELECT 1)
    SELECT * FROM t
    JOIN (SELECT id FROM orders o) AS sq ON 1
    JOIN customers c ON 1
    """
    err, refs = extract_physical_table_refs_from_select_sql(sql, dialect="mysql")
    assert err is None
    assert "t" not in refs
    assert "sq" not in refs
    assert "orders" in refs
    assert "customers" in refs


def test_extract_invalid_sql():
    err, refs = extract_physical_table_refs_from_select_sql("SELECT * FROM (", dialect="mysql")
    assert err is not None
    assert refs == {}


def test_extract_skips_oracle_dual_builtin():
    err, refs = extract_physical_table_refs_from_select_sql(
        "SELECT LEVEL FROM dual CONNECT BY LEVEL <= 3",
        dialect="oracle",
    )
    assert err is None
    assert refs == {}

    err2, refs2 = extract_physical_table_refs_from_select_sql(
        """
        WITH all_months AS (
          SELECT TO_CHAR(ADD_MONTHS(DATE '2025-12-01', LEVEL - 1), 'YYYY-MM') AS month_label
          FROM dual
          CONNECT BY LEVEL <= 7
        )
        SELECT am.month_label, c.total FROM all_months am
        LEFT JOIN clue_monthly c ON am.month_label = c.month_label
        """,
        dialect="oracle",
    )
    assert err2 is None
    assert "dual" not in refs2
    assert "clue_monthly" in refs2


def test_extract_skips_system_schema_tables():
    err, refs = extract_physical_table_refs_from_select_sql(
        "SELECT name FROM system.tables",
        dialect="clickhouse",
    )
    assert err is None
    assert refs == {}

    err2, refs2 = extract_physical_table_refs_from_select_sql(
        "SELECT table_name FROM information_schema.tables",
        dialect="mysql",
    )
    assert err2 is None
    assert refs2 == {}


def test_extract_keeps_non_system_schema_qualified_table():
    err, refs = extract_physical_table_refs_from_select_sql(
        "SELECT * FROM other_schema.allowed_table",
        dialect="mysql",
    )
    assert err is None
    assert refs["allowed_table"] == "allowed_table"
