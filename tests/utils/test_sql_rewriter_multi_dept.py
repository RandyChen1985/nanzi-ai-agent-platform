"""行级权限条件里的多值维度替换。

`{user.dept_codes}` 的语义是「一组部门 code」，模板写成
`dept_code IN ({user.dept_codes})`，替换后必须是 `'A', 'B'` 这种**不带括号**的列表，
由模板自己提供括号。

三个必须钉住的边界：

1. **空列表**：`IN ()` 是语法错误。替换成 `NULL` 让条件恒假（"没有部门 = 看不到数据"），
   而不是让整个查询炸掉；
2. **转义**：部门 code 里出现单引号必须双写，否则能拼出任意 SQL；
3. **单值行为不变**：既有的 `{user.dept_code}` 继续带引号替换。
"""

from __future__ import annotations

import pytest

from app.services.ai.rewriters.sql_rewriter import SQLRewriter

pytestmark = pytest.mark.no_infrastructure


def _rewrite(condition: str, context: dict, dialect: str = "mysql") -> str:
    rewriter = SQLRewriter(dialect=dialect)
    return rewriter.rewrite("SELECT * FROM t_sales", [{"condition": condition}], context)


def test_list_value_renders_as_quoted_csv():
    sql = _rewrite("dept_code IN ({user.dept_codes})", {"dept_codes": ["EAST", "WEST"]})
    assert "IN ('EAST', 'WEST')" in sql


def test_list_value_is_deduplicated_preserving_order():
    sql = _rewrite("dept_code IN ({user.dept_codes})", {"dept_codes": ["B", "A", "B"]})
    assert "IN ('B', 'A')" in sql


def test_empty_list_renders_as_null_so_condition_is_false():
    sql = _rewrite("dept_code IN ({user.dept_codes})", {"dept_codes": []})
    assert "IN (NULL)" in sql
    assert "IN ()" not in sql


def test_list_value_escapes_single_quotes():
    sql = _rewrite("dept_code IN ({user.dept_codes})", {"dept_codes": ["O'Brien"]})
    assert "O''Brien" in sql


def test_scalar_string_still_renders_with_quotes():
    """既有规则（主部门）零改动。"""
    sql = _rewrite("dept_code = {user.dept_code}", {"dept_code": "EAST"})
    assert "dept_code = 'EAST'" in sql


def test_scalar_single_quote_escaped():
    sql = _rewrite("dept_code = {user.dept_code}", {"dept_code": "O'Brien"})
    assert "O''Brien" in sql


def test_subtree_list_works_in_in_clause():
    sql = _rewrite(
        "dept_code IN ({user.dept_codes_subtree})",
        {"dept_codes_subtree": ["EAST", "EAST-SH"]},
    )
    assert "IN ('EAST', 'EAST-SH')" in sql


def test_list_with_blank_entries_is_filtered():
    sql = _rewrite("dept_code IN ({user.dept_codes})", {"dept_codes": ["EAST", "", "  ", "WEST"]})
    assert "IN ('EAST', 'WEST')" in sql


def test_list_variable_tolerates_outer_quotes():
    """管理员照标量写法写 `IN ('{user.dept_codes}')` 时不能拼出 ''A', 'B''。"""
    from app.services.ai.rewriters.sql_rewriter import SQLRewriter

    rendered = SQLRewriter()._replace_user_variables(
        "dept_code IN ('{user.dept_codes}')", {"dept_codes": ["A", "B"]}
    )

    assert rendered == "dept_code IN ('A', 'B')"


def test_empty_list_inside_quotes_still_becomes_null():
    from app.services.ai.rewriters.sql_rewriter import SQLRewriter

    rendered = SQLRewriter()._replace_user_variables(
        "dept_code IN ('{user.dept_codes}')", {"dept_codes": []}
    )

    assert rendered == "dept_code IN (NULL)"
