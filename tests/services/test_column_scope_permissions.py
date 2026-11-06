"""字段级（列级）数据权限：从配置解析到执行期拦截的整条链路。

设计要点：

1. **配置形态**：复用 ``ai_agent_resource_permissions.scope`` 这个 JSON 列，
   追加 ``columns``：``{table_id: {"mode": "include"|"exclude", "names": [...]}}``。
   没写 ``columns`` 的表 = 全部字段，历史数据零迁移。

2. **多来源取交集**：同一张表，用户级 / 角色级 / 全员三条来源只要有**一条**排除了
   某个列，这列就不可见。方向与表级相反 —— 表级「全员基线不可收窄」保证不能凭空
   少给权限；字段级反过来，全员排除掉的 id_card 不能被个人配置重新放开。

3. **执行期拒绝而不是剔除**：剔除字段是静默改变语义（``WHERE id_card LIKE '110%'``
   一旦少了 id_card，结果就从「北京客户」变成「全部客户」），所以 fail-closed。
"""
from __future__ import annotations

import pytest

from app.services.dataset_scope_service import (
    COL_EXCLUDE,
    COL_INCLUDE,
    ColumnRule,
    DatasetScope,
    _parse_columns,
    _parse_scope,
    build_dataset_scope,
)


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------


def test_parse_columns_reads_include_and_exclude() -> None:
    rules = _parse_columns(
        {
            "12": {"mode": "exclude", "names": ["ID_CARD", "mobile"]},
            "15": {"mode": "include", "names": ["task_id"]},
        }
    )
    assert rules[12].mode == COL_EXCLUDE
    # 列名统一小写，避免配置写大写就失效
    assert rules[12].names == frozenset({"id_card", "mobile"})
    assert rules[15].mode == COL_INCLUDE
    assert rules[15].names == frozenset({"task_id"})


def test_parse_columns_drops_noop_and_malformed() -> None:
    """exclude 空白名单 = 什么都没排除，丢掉；非法 mode / 非 dict 忽略。"""
    rules = _parse_columns(
        {
            "1": {"mode": "exclude", "names": []},
            "2": {"mode": "bogus", "names": ["x"]},
            "3": "not-a-dict",
            "bad-key": {"mode": "include", "names": ["y"]},
        }
    )
    assert rules == {}


def test_parse_columns_keeps_empty_include_as_fail_closed() -> None:
    """include 空白名单 = 一个字段都不许，这是有意义的配置，必须保留。"""
    rules = _parse_columns({"7": {"mode": "include", "names": []}})
    assert rules[7].mode == COL_INCLUDE
    assert rules[7].names == frozenset()


def test_parse_scope_keeps_columns_in_all_mode() -> None:
    """「全部表 + 挖掉几列」是最常见的组合，mode=all 时不能把 columns 一起丢掉。"""
    mode, tables, columns = _parse_scope(
        {"mode": "all", "columns": {"9": {"mode": "exclude", "names": ["id_card"]}}}
    )
    assert mode == "all"
    assert tables == frozenset()
    assert columns[9].names == frozenset({"id_card"})


def test_parse_scope_backward_compatible() -> None:
    """历史授权没有 columns，解析结果必须是空（= 全部字段）。"""
    assert _parse_scope(None)[2] == {}
    assert _parse_scope({"mode": "list", "tables": [1, 2]})[2] == {}


# ---------------------------------------------------------------------------
# 规则求值
# ---------------------------------------------------------------------------


def test_include_rule_keeps_only_listed_columns() -> None:
    rule = ColumnRule(mode=COL_INCLUDE, names=frozenset({"id", "name"}))
    assert rule.apply(["id", "name", "id_card"]) == {"id", "name"}


def test_exclude_rule_removes_listed_columns() -> None:
    rule = ColumnRule(mode=COL_EXCLUDE, names=frozenset({"id_card"}))
    assert rule.apply(["id", "name", "id_card"]) == {"id", "name"}


def test_visible_columns_intersects_multiple_rules() -> None:
    """两个来源：用户级 include{id,name} + 全员 exclude{id_card} → 取交集。"""
    scope = DatasetScope(
        dataset_id=1,
        mode="all",
        table_ids=frozenset(),
        column_rules={
            9: (
                ColumnRule(mode=COL_INCLUDE, names=frozenset({"id", "name"})),
                ColumnRule(mode=COL_EXCLUDE, names=frozenset({"id_card"})),
            )
        },
    )
    assert scope.visible_columns(9, ["id", "name", "id_card", "mobile"]) == {"id", "name"}


def test_visible_columns_none_when_no_rule() -> None:
    """没配规则 = 不受字段级限制，返回 None 而不是「全部列」，
    调用方据此区分「没限制」和「限制后恰好剩全部」。"""
    scope = DatasetScope(dataset_id=1, mode="all", table_ids=frozenset())
    assert scope.visible_columns(9, ["a", "b"]) is None


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------


def _item(subject: str, mode: str, tables: set, columns: dict) -> dict:
    return {
        "subject": subject,
        "mode": mode,
        "table_ids": frozenset(tables),
        "column_rules": columns,
        "row_policy": "inherit",
        "row_condition": None,
    }


def test_build_merges_user_and_all_column_rules() -> None:
    scope = build_dataset_scope(
        1,
        [
            _item("user", "list", {9, 10}, {9: ColumnRule(COL_INCLUDE, frozenset({"id", "name"}))}),
            _item("all", "all", set(), {9: ColumnRule(COL_EXCLUDE, frozenset({"id_card"}))}),
        ],
    )
    # 表 9：include{id,name} ∩ exclude{id_card} = {id,name}
    assert scope.visible_columns(9, ["id", "name", "id_card", "mobile"]) == {"id", "name"}
    # 表 10：只有用户级 exclude，没有全员规则
    assert scope.visible_columns(10, ["x", "y"]) is None


def test_build_ignores_rules_for_tables_source_cannot_see() -> None:
    """来源配了它自己都看不到的表的字段规则 → 不参与合并（配置残留）。"""
    scope = build_dataset_scope(
        1,
        [
            _item("user", "list", {9}, {10: ColumnRule(COL_EXCLUDE, frozenset({"x"}))}),
            _item("all", "list", {10}, {}),
        ],
    )
    assert scope.visible_columns(10, ["x", "y"]) is None


def test_build_without_columns_has_no_rules() -> None:
    scope = build_dataset_scope(1, [_item("user", "all", set(), {})])
    assert scope.column_rules == {}
    assert scope.visible_columns(1, ["a"]) is None


# ---------------------------------------------------------------------------
# schema 层：只输出可见列
# ---------------------------------------------------------------------------


_CHUNK = """table_name: ods_crm_customer
table_desc: CRM 客户原始表
dataset: ads
columns:
- name: customer_id
  type: bigint
- name: id_card
  type: varchar
- name: mobile
  type: varchar
"""


def test_trim_chunk_columns_drops_hidden_columns() -> None:
    from app.services.chatbi_dataset_schema_service import _trim_chunk_columns

    trimmed, hidden = _trim_chunk_columns(
        _CHUNK, (ColumnRule(COL_EXCLUDE, frozenset({"id_card", "mobile"})),)
    )
    assert "customer_id" in trimmed
    assert "id_card" not in trimmed
    assert "mobile" not in trimmed
    assert sorted(hidden) == ["id_card", "mobile"]


def test_trim_chunk_columns_noop_without_rule() -> None:
    from app.services.chatbi_dataset_schema_service import _trim_chunk_columns

    assert _trim_chunk_columns(_CHUNK, ())[0] == _CHUNK


def test_trim_chunk_columns_keeps_header_comments() -> None:
    from app.services.chatbi_dataset_schema_service import _trim_chunk_columns

    chunk = "# 说明：这是注释\n" + _CHUNK
    trimmed, hidden = _trim_chunk_columns(
        chunk, (ColumnRule(COL_EXCLUDE, frozenset({"id_card"})),)
    )
    assert hidden == ["id_card"]
    assert trimmed.startswith("# 说明：这是注释")


def test_column_scope_notice_counts_without_column_names() -> None:
    """提示只给数量，不回传被隐藏的列名（列名本身也是敏感信息）。"""
    from app.services.chatbi_dataset_schema_service import _append_column_scope_notice

    text = _append_column_scope_notice("SCHEMA", 3)
    assert "另有 3 个字段" in text
    assert _append_column_scope_notice("SCHEMA", 0) == "SCHEMA"


# ---------------------------------------------------------------------------
# SQL 层：列引用提取
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql,qualified,unqualified,star",
    [
        ("SELECT count(*) FROM t", {}, set(), set()),
        ("SELECT * FROM t", {}, set(), None),
        ("SELECT t.id, id_card FROM t", {"t": {"id"}}, {"id_card"}, set()),
        ("SELECT a.* FROM a JOIN b ON a.id=b.id", {"a": {"id"}, "b": {"id"}}, set(), {"a"}),
        ("SELECT 1 FROM t", {}, set(), set()),
    ],
)
def test_collect_sql_column_usage(sql, qualified, unqualified, star) -> None:
    import sqlglot

    from app.services.sql_query_execution_service import _collect_sql_column_usage

    expression = sqlglot.parse_one(sql, read="mysql")
    got_q, got_u, got_star = _collect_sql_column_usage(expression)
    assert got_q == qualified
    assert got_u == unqualified
    assert got_star == star


def test_column_rules_intersect_across_layers() -> None:
    """多条规则叠加取交集：include 与 exclude 同时命中时，任何一层禁止即不可见。"""
    include = ColumnRule(COL_INCLUDE, frozenset({"id", "name"}))
    exclude = ColumnRule(COL_EXCLUDE, frozenset({"id_card"}))
    scope = DatasetScope(
        dataset_id=1,
        mode="all",
        table_ids=frozenset(),
        column_rules={1: (include, exclude)},
    )
    visible = scope.visible_columns(1, ["id", "name", "id_card", "mobile"])
    assert visible == {"id", "name"}


# ---------------------------------------------------------------------------
# 改写：能剔的剔掉，不能剔的才拒绝
# ---------------------------------------------------------------------------


def _parse(sql: str):
    import sqlglot
    from sqlglot import exp

    from app.services.sql_query_execution_service import _physical_fragment_from_table

    expression = sqlglot.parse_one(sql, read="mysql")
    alias = {}
    for table in expression.find_all(exp.Table):
        physical = _physical_fragment_from_table(table)
        if not physical:
            continue
        key = str(table.alias or "").strip().lower() or physical.lower()
        alias[key] = physical.lower()
    return expression, alias


def _rewrite(sql: str, allowed: dict, full: dict | None = None) -> tuple[str, list[str]]:
    from app.services.sql_query_execution_service import _rewrite_select_projections

    expression, alias = _parse(sql)
    kept, dropped, blocked = _rewrite_select_projections(
        expression,
        alias_to_physical=alias,
        allowed_columns=allowed,
        full_columns=full if full is not None else {k: set(v) for k, v in allowed.items()},
        single_table=len(alias) == 1,
    )
    assert blocked is None, blocked
    expression.set("expressions", kept)
    return expression.sql(dialect="mysql"), dropped


def test_rewrite_drops_unauthorized_projection_column() -> None:
    """纯投影里无权字段直接剔掉，其余字段正常返回 —— 这正是用户要的行为。"""
    sql, dropped = _rewrite(
        "SELECT customer_name, id_card, mobile FROM ods_crm_customer",
        {"ods_crm_customer": {"customer_name", "mobile"}},
    )
    assert "id_card" not in sql
    assert "customer_name" in sql and "mobile" in sql
    assert dropped == ["id_card"]


def test_rewrite_expands_select_star_to_visible_columns() -> None:
    """`SELECT *` 展开成可见列，而不是一刀切拒绝。"""
    sql, _ = _rewrite(
        "SELECT * FROM ods_crm_customer",
        {"ods_crm_customer": {"customer_id", "customer_name"}},
    )
    assert "customer_id" in sql and "customer_name" in sql
    assert "*" not in sql


def test_rewrite_drops_whole_aggregate_when_it_touches_blocked_column() -> None:
    """聚合函数里含无权字段 → 整个聚合项剔除，不能变成 SUM() 这种坏 SQL。"""
    sql, dropped = _rewrite(
        "SELECT customer_name, SUM(id_card) AS s FROM t",
        {"t": {"customer_name"}},
    )
    assert "SUM" not in sql.upper()
    assert "customer_name" in sql
    assert dropped == ["id_card"]


def test_rewrite_keeps_allowed_columns_untouched() -> None:
    sql, dropped = _rewrite(
        "SELECT customer_name FROM ods_crm_customer",
        {"ods_crm_customer": {"customer_name", "id_card"}},
    )
    assert dropped == []
    assert "customer_name" in sql


def test_where_clause_columns_are_classified_as_unsafe() -> None:
    """WHERE / GROUP BY / ORDER BY / JOIN 里的无权字段不能剔，只能拒。"""
    from app.services.sql_query_execution_service import _column_clause

    cases = {
        "SELECT a FROM t WHERE b=1": ("b", "where"),
        "SELECT a FROM t GROUP BY b": ("b", "group"),
        "SELECT a FROM t ORDER BY b": ("b", "order"),
        "SELECT a FROM t JOIN u ON t.k=u.k": ("k", "join"),
        "SELECT SUM(a) FROM t": ("a", "projection"),
        "SELECT a, ROW_NUMBER() OVER (PARTITION BY b) FROM t": ("b", "window"),
    }
    import sqlglot
    from sqlglot import exp

    for sql, (column, expected) in cases.items():
        expression = sqlglot.parse_one(sql, read="mysql")
        found = [c for c in expression.find_all(exp.Column) if c.name == column]
        assert found, sql
        assert _column_clause(found[0]) == expected, sql


# ---------------------------------------------------------------------------
# 用户可见文案：字段级拒绝不能说成「没有这张表的权限」
# ---------------------------------------------------------------------------


def test_user_message_distinguishes_column_denial_from_table_denial() -> None:
    """用户能看到这张表、只是看不到某几列，文案必须说清楚是字段权限。"""
    from app.services.ai.chatbi_sql_user_messages import map_sql_tool_error_for_user

    presentation = map_sql_tool_error_for_user(
        "[Permission Denied] 无权访问表 'ods_crm_customer' 的字段 'id_card'：它们不在你的字段授权范围内。"
    )
    assert presentation.title == "无权访问部分字段"
    assert "id_card" in presentation.content
    assert "您可以查询数据表「ods_crm_customer」" in presentation.content
    # 不能退化成「没有表的权限」
    assert "您没有访问数据表" not in presentation.content


def test_user_message_keeps_table_denial_unchanged() -> None:
    from app.services.ai.chatbi_sql_user_messages import map_sql_tool_error_for_user

    presentation = map_sql_tool_error_for_user(
        "[Permission Denied] 无权访问表 'ai_agent_users'：它不在你在数据集 'ds1' 的表授权范围内。"
    )
    assert presentation.title == "无权访问数据表"


def test_column_denial_is_fatal_so_no_repair_loop() -> None:
    """字段级拒绝继承 [Permission Denied] 前缀 → fatal → 不进修复循环。

    权限拒绝重试没有意义（模型改写法也拿不到权限），只会白烧几轮 LLM。
    """
    from app.services.ai.runners.chatbi.sql_gates import is_sql_fatal_error

    assert is_sql_fatal_error(
        "[Permission Denied] 无权访问表 'ods_crm_customer' 的字段 'id_card'：..."
    ) is True
    assert is_sql_fatal_error(
        "[Permission Denied] 表 'ods_crm_customer' 有字段级权限限制，不能用 SELECT * 展开。"
    ) is True


# ---------------------------------------------------------------------------
# 用户文案：三种字段级拒绝要说得出区别
# ---------------------------------------------------------------------------


def test_message_for_condition_columns_explains_why_rejected() -> None:
    """WHERE 里用无权字段 → 必须解释「为什么不能像少一列那样省略」。"""
    from app.services.ai.chatbi_sql_user_messages import map_sql_tool_error_for_user

    presentation = map_sql_tool_error_for_user(
        "[Permission Denied] 这些字段不在你的授权范围内，且它们出现在筛选/分组/排序条件里，"
        "删掉会改变结果口径，因此本次查询已终止：筛选条件（WHERE） 里用到了 'id_card'。"
    )
    assert presentation.title == "查询条件用到了无权字段"
    assert "id_card" in presentation.content
    assert "改变统计口径" in presentation.content


def test_message_for_all_columns_blocked() -> None:
    from app.services.ai.chatbi_sql_user_messages import map_sql_tool_error_for_user

    presentation = map_sql_tool_error_for_user(
        "[Permission Denied] 你请求的字段 'id_card'、'mobile' 都不在你的授权范围内，没有任何可返回的字段。"
    )
    assert presentation.title == "无权访问请求的字段"
    assert "id_card" in presentation.content


def test_omitted_columns_survive_to_frontend_notice() -> None:
    """被剔除的列要能随 permission_notice 传到前端，否则用户以为查询漏了数据。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    insight = (root / "app/services/ai/runners/chatbi/insight_meta.py").read_text(encoding="utf-8")
    assert '"omitted_columns"' in insight

    editor = (root / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    assert "msg.omittedColumns" in editor
    assert "以下字段你没有查看权限" in editor


# ---------------------------------------------------------------------------
# 复审发现的缺陷回归（每条都对应一个真实的越权/静默错误）
# ---------------------------------------------------------------------------


def test_count_star_is_not_treated_as_wildcard() -> None:
    """`COUNT(*)` 的通配符在聚合函数内部，绝不能被展开成列清单。

    曾经的写法用 ``projection.find(exp.Star)`` 判断通配符，`Count(this=Star())`
    会命中，于是 ``SELECT COUNT(*) FROM t`` 被改写成 ``SELECT col1, col2 FROM t``
    —— 一条计数查询变成全量明细导出。
    """
    from app.services.sql_query_execution_service import _is_wildcard_projection

    import sqlglot

    for sql, expected in (
        ("SELECT COUNT(*) FROM t", False),
        ("SELECT a, COUNT(*) FROM t", False),
        ("SELECT COUNT(t.a) FROM t", False),
        ("SELECT * FROM t", True),
        ("SELECT t.* FROM t", True),
    ):
        expression = sqlglot.parse_one(sql, read="mysql")
        assert _is_wildcard_projection(expression.selects[0]) is expected, sql


def test_count_star_survives_rewrite_untouched() -> None:
    sql, dropped = _rewrite("SELECT a, COUNT(*) AS c FROM t", {"t": {"a", "b"}})
    assert "COUNT(*)" in sql
    assert dropped == []


def test_multi_table_bare_star_is_rejected_not_rewritten() -> None:
    """多表 JOIN + 不带表名的 `*` 无法确定列序，必须拒绝而不是硬展开。"""
    import sqlglot
    from sqlglot import exp

    from app.services.sql_query_execution_service import (
        _physical_fragment_from_table,
        _rewrite_select_projections,
    )

    expression = sqlglot.parse_one("SELECT * FROM t JOIN u ON t.k=u.k", read="mysql")
    alias = {}
    for table in expression.find_all(exp.Table):
        physical = _physical_fragment_from_table(table)
        alias[str(table.alias or "").strip().lower() or physical.lower()] = physical.lower()
    kept, dropped, blocked = _rewrite_select_projections(
        expression,
        alias_to_physical=alias,
        allowed_columns={"t": {"a"}, "u": {"k"}},
        single_table=False,
    )
    assert blocked == "multi_table_star"
    assert kept == [] and dropped == []


def test_positional_ordering_is_detected() -> None:
    """`ORDER BY 2` / `GROUP BY 1` 按序号引用列，删列后会错位，只能拒绝。"""
    import sqlglot

    from app.services.sql_query_execution_service import _has_positional_ordering

    assert _has_positional_ordering(sqlglot.parse_one("SELECT a FROM t ORDER BY 2", read="mysql"))
    assert _has_positional_ordering(sqlglot.parse_one("SELECT a FROM t GROUP BY 1", read="mysql"))
    assert not _has_positional_ordering(sqlglot.parse_one("SELECT a FROM t ORDER BY a", read="mysql"))


def test_permission_notice_survives_without_row_filter() -> None:
    """只发生字段剔除、没有行级过滤时，「某某字段没权限」的提示不能丢。

    原实现把 ``row_filter_applied is True`` 当成唯一判据，导致最常见的情况
    （只有字段级收窄）提示静默消失，用户以为查询本身少查了东西。
    """
    import json

    from app.services.sql_query_execution_service import attach_permission_notice_to_json_result

    payload = json.loads(
        attach_permission_notice_to_json_result(
            json.dumps({"rows": [{"a": 1}]}), {"omitted_columns": ["id_card"]}
        )
    )
    assert payload["permission_notice"]["omitted_columns"] == ["id_card"]


def test_permission_notice_absent_when_nothing_to_say() -> None:
    import json

    from app.services.sql_query_execution_service import attach_permission_notice_to_json_result

    payload = json.loads(
        attach_permission_notice_to_json_result(json.dumps({"rows": [{"a": 1}]}), {})
    )
    assert "permission_notice" not in payload


def test_metrics_block_filters_blocked_columns() -> None:
    """指标块的 sql 带真实字段名：表可见但字段被挡时也要整条剔除。"""
    from app.services.chatbi_dataset_schema_service import _filter_metrics_chunk_by_table_scope
    from app.services.dataset_scope_service import COL_EXCLUDE, ColumnRule

    chunk = (
        "metrics_scope: 1\n"
        "metrics:\n"
        "- metric_name: 客户数\n"
        "  sql: SELECT count(*) FROM ods_crm_customer\n"
        "- metric_name: 身份证校验\n"
        "  sql: SELECT count(*) FROM ods_crm_customer WHERE id_card LIKE '1%'\n"
    )
    rules = {"ods_crm_customer": (ColumnRule(COL_EXCLUDE, frozenset({"id_card"})),)}
    kept = _filter_metrics_chunk_by_table_scope(chunk, {"ods_crm_customer"}, rules)
    assert "身份证校验" not in kept
    assert "客户数" in kept


def test_metrics_block_unchanged_without_column_rules() -> None:
    from app.services.chatbi_dataset_schema_service import _filter_metrics_chunk_by_table_scope

    chunk = (
        "metrics_scope: 1\n"
        "metrics:\n"
        "- metric_name: 身份证校验\n"
        "  sql: SELECT count(*) FROM ods_crm_customer WHERE id_card LIKE '1%'\n"
    )
    assert _filter_metrics_chunk_by_table_scope(chunk, {"ods_crm_customer"}) == chunk


def test_few_shot_example_with_blocked_column_is_dropped() -> None:
    from app.services.dataset_scope_service import (
        COL_EXCLUDE,
        ColumnRule,
        _example_hits_blocked_columns,
    )

    rules = {"ods_crm_customer": (ColumnRule(COL_EXCLUDE, frozenset({"id_card"})),)}
    assert _example_hits_blocked_columns(
        {"sql": "SELECT name FROM ods_crm_customer WHERE id_card LIKE '1%'"}, rules
    )
    assert not _example_hits_blocked_columns(
        {"sql": "SELECT name, mobile FROM ods_crm_customer"}, rules
    )


def test_role_column_rule_not_dropped_by_personal_grant() -> None:
    """表级「用户级覆盖角色级」不能顺带把角色层的字段排除也覆盖掉。

    否则「角色层排除 id_card + 再给某人加一条个人授权」就等于把角色的排除抹掉了。
    """
    from app.services.dataset_scope_service import COL_EXCLUDE, ColumnRule, build_dataset_scope

    items = [
        {
            "subject": "user",
            "mode": "list",
            "table_ids": frozenset({5}),
            "column_rules": {},
            "row_policy": "inherit",
            "row_condition": None,
        },
        {
            "subject": "role",
            "mode": "list",
            "table_ids": frozenset({5}),
            "column_rules": {5: ColumnRule(COL_EXCLUDE, frozenset({"id_card"}))},
            "row_policy": "inherit",
            "row_condition": None,
        },
    ]
    scope = build_dataset_scope(1, items)
    assert "id_card" not in scope.visible_columns(5, ["id", "name", "id_card"])


def test_select_star_rewrite_reports_hidden_columns() -> None:
    """`SELECT *` 展开后必须真的用改写结果，并且报出被隐藏的列。

    曾经的缺陷：星号展开时 `dropped` 为空 → 调用方 `if not dropped: return original`
    直接退回原始 `SELECT *`，被隐藏的列原样返回，字段级权限对 `*` 查询完全失效。
    """
    sql, dropped = _rewrite(
        "SELECT * FROM ods_crm_customer",
        {"ods_crm_customer": {"customer_id", "customer_name"}},
        full={"ods_crm_customer": {"customer_id", "customer_name", "id_card"}},
    )
    assert "*" not in sql
    assert dropped == ["id_card"]  # 必须非空，否则调用方会丢弃改写结果


def test_load_allowed_columns_handles_dict_candidates(monkeypatch) -> None:
    """candidates 是 {物理表名: [行]}；直接迭代 dict 会拿到字符串 key 而崩溃。"""
    import asyncio

    from app.services.sql_query_execution_service import _load_allowed_columns_by_table

    class _FakeRow:
        def __init__(self, values):
            self._values = values

        def __iter__(self):
            return iter(self._values)

    class _FakeResult:
        def all(self):
            return [_FakeRow((92, "id")), _FakeRow((92, "secret"))]

    class _FakeSession:
        async def execute(self, *_a, **_kw):
            return _FakeResult()

    from app.services.dataset_scope_service import COL_EXCLUDE, ColumnRule, DatasetScope

    scope = DatasetScope(
        dataset_id=1,
        mode="all",
        table_ids=frozenset(),
        column_rules={92: (ColumnRule(COL_EXCLUDE, frozenset({"secret"})),)},
    )
    allowed, full = asyncio.get_event_loop().run_until_complete(
        _load_allowed_columns_by_table(
            _FakeSession(),
            scopes={1: scope},
            candidates={"ai_agent_access_logs": [(92, 1, "ai_agent_meta")]},
        )
    )
    assert allowed["ai_agent_access_logs"] == {"id"}
    assert full["ai_agent_access_logs"] == {"id", "secret"}
