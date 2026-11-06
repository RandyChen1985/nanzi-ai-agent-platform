"""行级数据权限：复审发现的 fail-open 缺陷回归。

三条都属于「配了限制却看到全表」这一类，而且触发条件都是**最常见**的写法：

1. 无表前缀条件里出现以 ``IN`` / ``IS`` 结尾的字符串字面量（``status = 'ADMIN'``）；
2. 条件里出现 ``a.b`` 形态的文本（邮箱、``schema.table.col``）；
3. ``row_filter_config`` 某层没配规则（默认值就是 ``[]``）时被当成「不过滤」。

第 3 条尤其危险：前端「数据权限配置」面板的默认值就是 ``default_policy: []``，
管理员勾了「启用数据权限」但没填规则就保存 → 用户看到整表。
"""
from __future__ import annotations

import pytest

from app.services.ai.rewriters.sql_rewriter import SQLRewriter

_ORDERS = {"orders": {"id", "dept_code", "status", "amount", "email"}}


def _rewriter() -> SQLRewriter:
    rewriter = SQLRewriter()
    rewriter.table_metadata = {key: set(value) for key, value in _ORDERS.items()}
    return rewriter


# ---------------------------------------------------------------------------
# 1. 字符串字面量不能被当成列名
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "condition",
    [
        "status = 'ADMIN'",        # 'ADMIN' 里有 IN
        "dept_code = 'FIN'",       # 'FIN' 里有 IN
        "dept_code = 'THIS'",      # 'THIS' 里有 IS
        "dept_code = 'SALES'",     # 不含关键字，本来就该生效
    ],
)
def test_literal_ending_with_keyword_still_applies_condition(condition: str) -> None:
    sql = _rewriter().rewrite("SELECT * FROM orders", [{"condition": condition}], {})
    assert "WHERE" in sql.upper(), f"行级条件被静默丢弃: {condition} -> {sql}"


def test_numeric_condition_still_applies() -> None:
    sql = _rewriter().rewrite("SELECT * FROM orders", [{"condition": "dept_code = 12"}], {})
    assert "WHERE dept_code = 12" in sql


# ---------------------------------------------------------------------------
# 2. `a.b` 形态的字面量不能伪造出表名
# ---------------------------------------------------------------------------


def test_email_literal_does_not_fake_a_table() -> None:
    """`'boss@corp.com'` 曾被正则拆成表 `corp`，整条条件被丢掉。"""
    sql = _rewriter().rewrite(
        "SELECT * FROM orders", [{"condition": "orders.email <> 'boss@corp.com'"}], {}
    )
    assert "corp.com" in sql
    assert "WHERE" in sql.upper()


def test_qualified_condition_keeps_table_prefix() -> None:
    sql = _rewriter().rewrite("SELECT * FROM orders", [{"condition": "orders.dept_code = 'FIN'"}], {})
    assert "orders.dept_code" in sql


# ---------------------------------------------------------------------------
# 3. `row_filter_config` 空规则必须 fail-closed
# ---------------------------------------------------------------------------


def test_empty_config_denies_instead_of_allowing_all() -> None:
    filters = SQLRewriter.resolve_strategy(42, ["viewer"], None)
    assert filters == [{"condition": "1=0"}]


def test_empty_default_policy_denies() -> None:
    """前端面板的默认值就是 `default_policy: []`，不能等于「看全表」。"""
    config = {"user_overrides": {}, "role_policies": {}, "default_policy": []}
    assert SQLRewriter.resolve_strategy(42, ["viewer"], config) == [{"condition": "1=0"}]


def test_empty_user_override_denies() -> None:
    config = {"user_overrides": {"42": []}, "role_policies": {}, "default_policy": []}
    assert SQLRewriter.resolve_strategy(42, ["viewer"], config) == [{"condition": "1=0"}]


def test_role_with_no_rules_does_not_wipe_other_role_rules() -> None:
    """用户多挂一个「没配规则」的角色，不能把另一个角色的限制整体抹掉。"""
    config = {
        "user_overrides": {},
        "role_policies": {"viewer": [{"condition": "dept_code = 'FIN'"}], "ops": []},
        "default_policy": [{"condition": "1=0"}],
    }
    filters = SQLRewriter.resolve_strategy(42, ["viewer", "ops"], config)
    assert filters == [{"condition": "dept_code = 'FIN'"}]


def test_all_roles_empty_denies() -> None:
    config = {"user_overrides": {}, "role_policies": {"ops": []}, "default_policy": []}
    assert SQLRewriter.resolve_strategy(42, ["ops"], config) == [{"condition": "1=0"}]


def test_anonymous_still_denies() -> None:
    assert SQLRewriter.resolve_strategy(None, ["viewer"], {"default_policy": []}) == [
        {"condition": "1=0"}
    ]


def test_explicit_rules_still_apply() -> None:
    config = {
        "user_overrides": {},
        "role_policies": {"viewer": [{"condition": "dept_code = 'FIN'"}]},
        "default_policy": [{"condition": "1=0"}],
    }
    assert SQLRewriter.resolve_strategy(42, ["viewer"], config) == [{"condition": "dept_code = 'FIN'"}]
