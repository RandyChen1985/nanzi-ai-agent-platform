import pytest
import sqlglot
from sqlglot import exp

from app.services.ai.rewriters.sql_rewriter import SQLRewriteError, SQLRewriter

pytestmark = pytest.mark.no_infrastructure

def test_basic_rewrite():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT * FROM sys_server_assets"
    filters = [{"condition": "region_code = {user.region}"}]
    context = {"region": "SH"}
    
    rewritten = rewriter.rewrite(sql, filters, context)
    assert "WHERE" in rewritten
    assert "region_code = 'SH'" in rewritten

def test_multiple_filters():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT * FROM sys_server_assets WHERE cpu > 80"
    filters = [
        {"condition": "region_code = {user.region}"},
        {"condition": "status = 1"}
    ]
    context = {"region": "BJ"}
    
    rewritten = rewriter.rewrite(sql, filters, context)
    assert "cpu > 80" in rewritten
    assert "region_code = 'BJ'" in rewritten
    assert "status = 1" in rewritten
    assert rewritten.count("AND") >= 2

def test_subquery_rewrite():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT name FROM (SELECT * FROM assets) t"
    filters = [{"condition": "dept_id = {user.dept}"}]
    context = {"dept": 101}
    
    rewritten = rewriter.rewrite(sql, filters, context)
    # The filter should be injected into the subquery as well
    assert "dept_id = 101" in rewritten

def test_union_rewrite():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT id FROM table1 UNION ALL SELECT id FROM table2"
    filters = [{"condition": "org_id = 5"}]
    context = {} # No placeholders needed here
    
    rewritten = rewriter.rewrite(sql, filters, context)
    assert rewritten.count("WHERE org_id = 5") == 2

def test_table_qualified_filter_uses_query_alias():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT * FROM orders o"
    filters = [{"condition": "orders.dept_code = {user.dept}"}]
    context = {"dept": "D001"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert "o.dept_code = 'D001'" in rewritten
    assert "orders.dept_code" not in rewritten

def test_table_name_in_condition_is_case_insensitive():
    rewriter = SQLRewriter(
        dialect="clickhouse",
        table_metadata={"orders": {"dept_code"}},
    )
    sql = "SELECT * FROM orders o"
    filters = [{"condition": "Orders.dept_code = {user.dept}"}]
    context = {"dept": "D001"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert "o.dept_code = 'D001'" in rewritten

def test_table_filter_only_applies_to_matching_select_scope():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = (
        "SELECT * FROM orders o "
        "WHERE o.customer_id IN (SELECT c.id FROM customers c)"
    )
    filters = [{"condition": "orders.dept_code = {user.dept}"}]
    context = {"dept": "D001"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert rewritten.count("dept_code = 'D001'") == 1
    assert "o.dept_code = 'D001'" in rewritten
    assert "c.dept_code = 'D001'" not in rewritten

def test_different_table_filters_apply_to_their_own_select_scopes():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = (
        "SELECT * FROM orders o "
        "WHERE o.customer_id IN (SELECT c.id FROM customers c)"
    )
    filters = [
        {"condition": "orders.dept_code = {user.dept}"},
        {"condition": "customers.region_code = {user.region}"},
    ]
    context = {"dept": "D001", "region": "SH"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert "o.dept_code = 'D001'" in rewritten
    assert "c.region_code = 'SH'" in rewritten
    assert rewritten.count("dept_code = 'D001'") == 1
    assert rewritten.count("region_code = 'SH'") == 1

def test_unrelated_table_filter_is_skipped():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT * FROM customers c"
    filters = [{"condition": "orders.dept_code = {user.dept}"}]
    context = {"dept": "D001"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert "WHERE" not in rewritten
    assert "orders.dept_code" not in rewritten

def test_cte_filter_applies_inside_physical_table_scope_only():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = (
        "WITH recent_orders AS (SELECT * FROM orders o) "
        "SELECT * FROM recent_orders ro"
    )
    filters = [{"condition": "orders.dept_code = {user.dept}"}]
    context = {"dept": "D001"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert "o.dept_code = 'D001'" in rewritten
    assert rewritten.count("dept_code = 'D001'") == 1

def test_unqualified_filter_does_not_apply_to_outer_cte_alias_scope():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = (
        "WITH recent_orders AS (SELECT * FROM orders o) "
        "SELECT * FROM recent_orders ro"
    )
    filters = [{"condition": "dept_code = {user.dept}"}]
    context = {"dept": "D001"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert rewritten.count("dept_code = 'D001'") == 1
    assert "FROM recent_orders AS ro WHERE dept_code" not in rewritten

def test_target_table_filter_with_unqualified_condition_uses_matching_scope():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = (
        "SELECT * FROM orders o "
        "WHERE o.customer_id IN (SELECT c.id FROM customers c)"
    )
    filters = [{"target_table": "orders", "condition": "dept_code = {user.dept}"}]
    context = {"dept": "D001"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert rewritten.count("dept_code = 'D001'") == 1
    assert "SELECT c.id FROM customers AS c WHERE dept_code = 'D001'" not in rewritten

def test_unqualified_all_filter_skips_multi_table_subquery_scope():
    rewriter = SQLRewriter(
        dialect="clickhouse",
        table_metadata={"orders": {"dept_code"}, "customers": {"id"}},
    )
    sql = (
        "SELECT * FROM orders o "
        "WHERE o.customer_id IN (SELECT c.id FROM customers c)"
    )
    filters = [{"condition": "dept_code = {user.dept}"}]
    context = {"dept": "D001"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert rewritten.count("dept_code = 'D001'") == 1
    assert "o.dept_code = 'D001'" in rewritten
    assert "c.dept_code = 'D001'" not in rewritten
    assert "FROM customers AS c WHERE dept_code" not in rewritten

def test_rewrite_stats_reports_applied_condition_count():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = (
        "SELECT * FROM orders o "
        "WHERE o.customer_id IN (SELECT c.id FROM customers c)"
    )
    filters = [
        {"condition": "orders.dept_code = {user.dept}"},
        {"condition": "customers.region_code = {user.region}"},
    ]
    context = {"dept": "D001", "region": "SH"}
    stats: dict[str, int] = {}

    rewriter.rewrite(sql, filters, context, rewrite_stats=stats)

    assert stats.get("applied_rule_count") == 2

def test_strategy_resolution():
    config = {
        "user_overrides": {
            "1": [{"condition": "1=1"}]
        },
        "role_policies": {
            "manager": [{"condition": "dept_id = {user.dept}"}],
            "viewer": [{"condition": "id = {user.id}"}]
        },
        "default_policy": [{"condition": "1=0"}]
    }
    
    # User override test
    filters = SQLRewriter.resolve_strategy(1, ["manager"], config)
    assert filters == [{"condition": "1=1"}]
    
    # Role merge test
    filters = SQLRewriter.resolve_strategy(2, ["manager", "viewer"], config)
    assert len(filters) == 2
    
    # Default policy test
    filters = SQLRewriter.resolve_strategy(3, ["guest"], config)
    assert filters == [{"condition": "1=0"}]

def test_placeholder_replacer():
    rewriter = SQLRewriter()
    # Recommended way: use placeholder directly, rewriter handles quotes
    filters = [{"condition": "path LIKE {user.path}"}]
    context = {"path": "yovole/sh%"}
    
    prepared = rewriter._prepare_conditions(filters, context)
    assert len(prepared) > 0
    sql_cond = prepared[0].sql()
    assert "yovole/sh%" in sql_cond

def test_string_user_variable_escapes_single_quotes():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT * FROM sys_server_assets"
    filters = [{"condition": "dept_code = {user.dept}"}]
    context = {"dept": "O'Reilly"}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert "WHERE" in rewritten
    assert "dept_code = 'O''Reilly'" in rewritten

def test_string_user_variable_treats_injection_payload_as_literal():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT * FROM sys_server_assets"
    filters = [{"condition": "dept_code = {user.dept}"}]
    context = {"dept": "x' OR 1=1 --"}

    rewritten = rewriter.rewrite(sql, filters, context)

    where_expr = sqlglot.parse_one(rewritten, read="clickhouse").find(exp.Where).this
    assert isinstance(where_expr, exp.EQ)
    assert where_expr.expression.this == "x' OR 1=1 --"

def test_none_user_variable_rewrites_to_null_default_deny():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT * FROM sys_server_assets"
    filters = [{"condition": "dept_id = {user.dept}"}]
    context = {"dept": None}

    rewritten = rewriter.rewrite(sql, filters, context)

    assert "dept_id = NULL" in rewritten

def test_invalid_permission_condition_fails_closed():
    rewriter = SQLRewriter(dialect="clickhouse")
    sql = "SELECT * FROM sys_server_assets"
    filters = [{"condition": "dept_code = 'unterminated"}]

    with pytest.raises(SQLRewriteError):
        rewriter.rewrite(sql, filters, {})

def test_oracle_rewrite():
    # Oracle often uses double quotes for identifiers and has distinct syntax
    rewriter = SQLRewriter(dialect="oracle")
    sql = 'SELECT * FROM "USERS" WHERE "STATUS" = 1'
    filters = [{"condition": 'DEPT_ID = {user.dept}'}]
    context = {"dept": 500}
    
    rewritten = rewriter.rewrite(sql, filters, context)
    # sqlglot might normalize output, but it should contain the injected condition
    assert "DEPT_ID = 500" in rewritten
    assert "WHERE" in rewritten


# ---------------------------------------------------------------------------
# 行级条件在多表作用域里的归属判定：归属不明必须拒绝执行，不能静默丢弃
# ---------------------------------------------------------------------------


def test_unqualified_row_filter_ambiguous_across_tables_is_rejected():
    """两张表都含 dept_code 时无前缀条件无法归属，必须拒绝而不是丢掉条件放行。

    丢掉条件的后果是 SQL 原样执行并返回未隔离的全量数据——行级权限彻底失效，
    而且不报错，调用方完全无感。
    """
    from app.services.ai.rewriters.sql_rewriter import SQLPermissionError

    rewriter = SQLRewriter(
        dialect="mysql",
        table_metadata={
            "orders": {"dept_code", "amount"},
            "depts": {"dept_code", "dept_name"},
        },
    )
    sql = "SELECT o.amount FROM orders o JOIN depts d ON o.dept_code = d.dept_code"
    filters = [{"condition": "dept_code = {user.dept_code}"}]

    with pytest.raises(SQLPermissionError):
        rewriter.rewrite(sql, filters, {"dept_code": "FIN"})


def test_unqualified_row_filter_unique_owner_still_applies():
    """只有一张表含该列时归属明确，条件照常注入（不能因为收紧而误伤合法查询）。"""
    rewriter = SQLRewriter(
        dialect="mysql",
        table_metadata={
            "orders": {"dept_code", "amount"},
            "depts": {"dept_name"},
        },
    )
    sql = "SELECT o.amount FROM orders o JOIN depts d ON o.dept_code = d.dept_code"
    filters = [{"condition": "dept_code = {user.dept_code}"}]

    rewritten = rewriter.rewrite(sql, filters, {"dept_code": "FIN"})

    assert "dept_code = 'FIN'" in rewritten


def test_unqualified_row_filter_without_metadata_is_rejected_on_multi_table():
    """没有表元数据时多表下无从判定归属，只能拒绝——否则就是静默放行。"""
    from app.services.ai.rewriters.sql_rewriter import SQLPermissionError

    rewriter = SQLRewriter(dialect="mysql")
    sql = "SELECT o.amount FROM orders o JOIN depts d ON o.dept_code = d.dept_code"
    filters = [{"condition": "dept_code = {user.dept_code}"}]

    with pytest.raises(SQLPermissionError):
        rewriter.rewrite(sql, filters, {"dept_code": "FIN"})


def test_unqualified_row_filter_ignores_dimension_subquery_without_column():
    """维度表子查询里没有该列时不能拒绝整条查询。

    子作用域独立判定，若把「本作用域无此列」当成归属歧义，任何带维度表子查询的
    合法报表都会被拒——比原来的静默放行更糟。
    """
    rewriter = SQLRewriter(
        dialect="mysql",
        table_metadata={
            "fact_sales": {"dept_code", "amount"},
            "dim_date": {"cal_date"},
        },
    )
    sql = "SELECT * FROM fact_sales f JOIN (SELECT cal_date FROM dim_date) d ON 1 = 1"
    filters = [{"condition": "dept_code = {user.dept_code}"}]

    rewritten = rewriter.rewrite(sql, filters, {"dept_code": "FIN"})

    assert "dept_code = 'FIN'" in rewritten
