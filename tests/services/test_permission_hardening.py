"""权限加固回归：本次修复的 5 类越权/放行路径各留一条可复现的用例。

覆盖点：
1. 多表 SQL 里不带表前缀的列不再默认放行（归属唯一才放行，歧义/未知一律拒绝）；
2. 聚合/表达式别名（``SUM(x) AS total`` 在 ORDER BY/GROUP BY/HAVING 里引用）不被误杀；
3. 配了字段级规则但读不到列清单时 fail-closed，而不是「全部字段可见」；
4. UNION 行级条件只覆盖一个分支时拒绝执行；
5. 数据集删除时同步清理多态权限行；
6. 智能体工具级绑定的数据集范围优先于用户手选（schema 与执行同口径）。
"""
from __future__ import annotations

import pytest

from app.services.dataset_scope_service import (
    COL_EXCLUDE,
    COL_INCLUDE,
    ColumnRule,
    DatasetScope,
)

# ---------------------------------------------------------------------------
# 字段级：多表未限定列归属
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, values):
        self._values = values

    def __iter__(self):
        return iter(self._values)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return [_Row(row) for row in self._rows]

    def scalar_one_or_none(self):
        return self._rows[0][0] if self._rows else None


class _ScopeSession:
    """只回答字段级门禁的两条查询：表归属 + 列清单。"""

    def __init__(self, tables, columns):
        # tables: [(table_id, physical_name, dataset_id, dataset_name)]
        self.tables = tables
        self.columns = columns

    async def execute(self, stmt, *_args, **_kwargs):
        text = str(stmt)
        if "meta_columns" in text:
            return _Result(self.columns)
        return _Result(self.tables)


async def _apply(sql: str, *, tables, columns, table_rules):
    from app.services import sql_query_execution_service as sql_service

    physical_by_id = {row[0]: row[1] for row in tables}
    rules_by_key = {
        (1, physical_by_id[table_id]): tuple(rules)
        for table_id, rules in table_rules.items()
        if table_id in physical_by_id
    }

    async def fake_column_rules(_session, _scopes):
        return rules_by_key

    import app.services.dataset_scope_service as scope_service

    original_rules = scope_service.column_rules_by_table_name
    scope_service.column_rules_by_table_name = fake_column_rules
    try:
        scope = DatasetScope(
            dataset_id=1,
            mode="all",
            table_ids=frozenset(),
            column_rules=table_rules,
        )
        scopes = {1: scope}
        return await sql_service.apply_column_scope_to_select_sql(
            _ScopeSession(tables, columns),
            sql=sql,
            dialect="mysql",
            user_id_eff=4,
            scopes=scopes,
        )
    finally:
        scope_service.column_rules_by_table_name = original_rules


def _rules(table_id: int, mode: str, names: set[str]):
    return {table_id: (ColumnRule(mode, frozenset(names)),)}


_ORDERS = (1, "orders", 1, "ds1")
_CUSTOMERS = (2, "customers", 1, "ds1")
_COLUMNS = [
    (1, "dept"),
    (1, "amount"),
    (1, "phone"),
    (1, "secret"),
    (2, "cust_id"),
    (2, "phone"),
]


@pytest.mark.asyncio
async def test_multi_table_unqualified_column_ambiguous_denied() -> None:
    """`phone` 在两张表里都有：SQL 本身歧义，不能猜一张表就放行。"""
    sql, dropped, err = await _apply(
        "SELECT o.dept FROM orders o JOIN customers c ON o.dept = c.cust_id "
        "WHERE phone = 'x'",
        tables=[_ORDERS, _CUSTOMERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_EXCLUDE, {"secret"}),
    )
    assert sql.startswith("SELECT o.dept")
    assert dropped == []
    assert err is not None and "[Permission Denied]" in err
    assert "无法判定属于哪张表" in err


@pytest.mark.asyncio
async def test_multi_table_unqualified_column_unique_owner_allowed() -> None:
    """归属唯一、且目标表没有字段收窄 → 正常放行，不能一刀切拒绝。"""
    sql, dropped, err = await _apply(
        "SELECT o.dept FROM orders o JOIN customers c ON o.dept = c.cust_id "
        "WHERE cust_id = 1",
        tables=[_ORDERS, _CUSTOMERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_EXCLUDE, {"secret"}),
    )
    assert err is None
    assert "cust_id" in sql
    assert dropped == []


@pytest.mark.asyncio
async def test_multi_table_unqualified_column_unique_owner_denied() -> None:
    """归属唯一但正好落在被排除的列上 → 拒绝（筛选位置不能删列）。"""
    _, _, err = await _apply(
        "SELECT o.dept FROM orders o JOIN customers c ON o.dept = c.cust_id "
        "WHERE secret = 'x'",
        tables=[_ORDERS, _CUSTOMERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_EXCLUDE, {"secret"}),
    )
    assert err is not None and "[Permission Denied]" in err
    assert "secret" in err


@pytest.mark.asyncio
async def test_order_by_aggregate_alias_is_allowed() -> None:
    """`SUM(amount) AS total ... ORDER BY total`：total 不是物理列，不能被判越权。"""
    sql, dropped, err = await _apply(
        "SELECT dept, SUM(amount) AS total FROM orders "
        "GROUP BY dept ORDER BY total DESC",
        tables=[_ORDERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_INCLUDE, {"dept", "amount"}),
    )
    assert err is None
    assert "SUM(amount) AS total" in sql
    assert dropped == []


@pytest.mark.asyncio
async def test_group_by_and_having_alias_are_allowed() -> None:
    _, _, err = await _apply(
        "SELECT dept, SUM(amount) AS total FROM orders "
        "GROUP BY dept HAVING total > 100 ORDER BY total",
        tables=[_ORDERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_INCLUDE, {"dept", "amount"}),
    )
    assert err is None


@pytest.mark.asyncio
async def test_order_by_alias_of_dropped_projection_is_denied() -> None:
    """别名背后的字段被隐藏 → 别名会被剔除，再引用它就是悬空，必须拒绝。"""
    _, _, err = await _apply(
        "SELECT dept, SUM(secret) AS total FROM orders "
        "GROUP BY dept ORDER BY total",
        tables=[_ORDERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_INCLUDE, {"dept"}),
    )
    assert err is not None and "[Permission Denied]" in err
    assert "别名" in err
    assert "total" in err


@pytest.mark.asyncio
async def test_where_cannot_reference_select_alias() -> None:
    """别名豁免只给 ORDER BY / GROUP BY / HAVING；WHERE 里出现非物理列仍要拒绝。"""
    _, _, err = await _apply(
        "SELECT dept, SUM(amount) AS total FROM orders WHERE total > 10 GROUP BY dept",
        tables=[_ORDERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_INCLUDE, {"dept", "amount"}),
    )
    assert err is not None and "[Permission Denied]" in err
    assert "total" in err
    assert "WHERE / JOIN ON 不能引用 SELECT 里的别名" in err


@pytest.mark.asyncio
async def test_cte_qualified_column_uses_source_table_rules() -> None:
    """`WITH t AS (SELECT * FROM orders) SELECT t.secret FROM t` 不能被别名绕过。"""
    _, _, err = await _apply(
        "WITH t AS (SELECT * FROM orders) SELECT t.secret FROM t",
        tables=[_ORDERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_EXCLUDE, {"secret"}),
    )
    assert err is not None and "[Permission Denied]" in err
    assert "secret" in err


@pytest.mark.asyncio
async def test_chained_cte_qualified_column_still_uses_source_rules() -> None:
    """`b AS (SELECT * FROM a)` 的别名要一路还原到物理表，不能只还原一层。"""
    _, _, err = await _apply(
        "WITH a AS (SELECT * FROM orders), b AS (SELECT * FROM a) "
        "SELECT b.secret FROM b",
        tables=[_ORDERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_EXCLUDE, {"secret"}),
    )
    assert err is not None and "[Permission Denied]" in err
    assert "secret" in err


@pytest.mark.asyncio
async def test_cte_qualified_allowed_column_passes() -> None:
    _, _, err = await _apply(
        "WITH t AS (SELECT * FROM orders) SELECT t.dept FROM t",
        tables=[_ORDERS],
        columns=_COLUMNS,
        table_rules=_rules(1, COL_INCLUDE, {"dept", "amount"}),
    )
    assert err is None


@pytest.mark.asyncio
async def test_derived_table_with_multiple_sources_is_denied() -> None:
    """派生表来自 UNION 的多张物理表，无法归属字段规则 → 拒绝而不是放行。"""
    _, _, err = await _apply(
        "SELECT t.secret FROM ("
        "SELECT secret FROM orders UNION SELECT secret FROM orders_archive"
        ") t",
        tables=[_ORDERS, (3, "orders_archive", 1, "ds1")],
        columns=_COLUMNS + [(3, "secret"), (3, "id")],
        table_rules=_rules(1, COL_EXCLUDE, {"secret"}),
    )
    assert err is not None and "[Permission Denied]" in err


@pytest.mark.asyncio
async def test_missing_column_metadata_is_fail_closed() -> None:
    """配了字段规则却读不到列清单：历史实现按「全开」处理，等于权限静默失效。"""
    _, _, err = await _apply(
        "SELECT dept FROM orders",
        tables=[_ORDERS],
        columns=[],  # 元数据没有列
        table_rules=_rules(1, COL_EXCLUDE, {"secret"}),
    )
    assert err is not None and "[Permission Denied]" in err
    assert "元数据" in err


def test_resolve_column_owner_reports_ambiguous_and_unknown() -> None:
    from app.services.sql_query_execution_service import _resolve_column_owner

    alias = {"o": "orders", "c": "customers"}
    full = {"orders": {"phone", "dept"}, "customers": {"phone", "cust_id"}}

    assert _resolve_column_owner("o", "phone", alias_to_physical=alias, full_columns=full, single_table=False) == (
        "orders",
        None,
    )
    assert _resolve_column_owner("", "phone", alias_to_physical=alias, full_columns=full, single_table=False) == (
        None,
        "ambiguous",
    )
    assert _resolve_column_owner("", "nope", alias_to_physical=alias, full_columns=full, single_table=False) == (
        None,
        "unknown",
    )


@pytest.mark.asyncio
async def test_field_gate_denies_when_scope_resolution_raises() -> None:
    """授权解析异常不能退化成「跳过字段级」——这正是原来的越权路径。"""
    from app.services import sql_query_execution_service as sql_service

    async def boom(*_args, **_kwargs):
        raise RuntimeError("授权库不可用")

    sql, dropped, err = await sql_service.apply_column_scope_to_select_sql(
        None,
        sql="SELECT id FROM orders",
        dialect="mysql",
        user_id_eff=4,
        scopes=boom,
    )
    assert err is not None and "[Permission Denied]" in err
    assert "无法解析字段级权限" in err
    assert sql == "SELECT id FROM orders" and dropped == []


# ---------------------------------------------------------------------------
# 授权只解析一次（性能回归）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dataset_scopes_resolved_once_per_execution(monkeypatch) -> None:
    """四道门禁共用一份授权：一次执行只允许查一次库。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.services.dataset_scope_service as scope_service
    from app.services import sql_query_execution_service as sql_service

    calls = {"n": 0}

    async def fake_resolve(_session, _user_id, **_kwargs):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(scope_service, "resolve_user_dataset_scopes", fake_resolve)

    fake_ds = SimpleNamespace(
        id=1, name="ds1", enable_data_perm=False, row_filter_config={}
    )

    async def fake_get_dataset(*_args, **_kwargs):
        return fake_ds

    async def pass_gate(*_args, **kwargs):
        provider = kwargs.get("scopes")
        if callable(provider):
            await provider()
        return None

    async def fake_sandbox(*_args, **_kwargs):
        return SimpleNamespace(allowed=True, optimized_sql="", message="")

    monkeypatch.setattr(sql_service.MetadataService, "get_dataset_by_name", fake_get_dataset)
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.enforce_physical_table_permissions",
        pass_gate,
    )
    monkeypatch.setattr(
        "app.services.ai.chatbi_sql_query_binding.enforce_dataset_table_scope", pass_gate
    )
    monkeypatch.setattr(scope_service, "enforce_table_scope_for_refs", pass_gate)
    monkeypatch.setattr(
        "app.services.ai.sql_sandbox_gate.SQLSandboxGate.verify_and_optimize", fake_sandbox
    )

    result = await sql_service.execute_sql_query_core(
        session=AsyncMock(),
        sql="SELECT id FROM orders",
        data_source="mysql_oa",
        dataset_name="ds1",
        user_id=4,
        user_dimensions={"user_name": "u", "dept_code": "FIN"},
        dry_run=True,
    )

    assert "[DRY_RUN]" in result
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 行级：UNION 分支未覆盖 → 拒绝
# ---------------------------------------------------------------------------


def test_union_branch_without_row_filter_is_rejected() -> None:
    from app.services.ai.rewriters.sql_rewriter import SQLPermissionError, SQLRewriter

    rewriter = SQLRewriter(
        dialect="mysql",
        table_metadata={"orders": {"id", "dept_code"}, "orders_archive": {"id", "dept_code"}},
    )
    with pytest.raises(SQLPermissionError) as exc_info:
        rewriter.rewrite(
            "SELECT id FROM orders UNION SELECT id FROM orders_archive",
            filters=[{"target_table": "orders", "condition": "orders.dept_code = 'FIN'"}],
            user_context={"dept_code": "FIN"},
        )
    assert "[Permission Denied]" in str(exc_info.value)
    assert "UNION" in str(exc_info.value)


def test_union_all_branches_filtered_is_allowed() -> None:
    from app.services.ai.rewriters.sql_rewriter import SQLRewriter

    rewriter = SQLRewriter(
        dialect="mysql",
        table_metadata={"orders": {"id", "dept_code"}, "orders_archive": {"id", "dept_code"}},
    )
    sql = rewriter.rewrite(
        "SELECT id FROM orders UNION ALL SELECT id FROM orders_archive",
        filters=[
            {"target_table": "orders", "condition": "orders.dept_code = 'FIN'"},
            {"target_table": "orders_archive", "condition": "orders_archive.dept_code = 'FIN'"},
        ],
        user_context={"dept_code": "FIN"},
    )
    assert sql.count("dept_code") == 2


# ---------------------------------------------------------------------------
# 数据集删除：清理孤儿权限行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_dataset_removes_resource_permissions(monkeypatch) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services import metadata_service as ms

    async def fake_get_dataset(*_args, **_kwargs):
        return None

    async def fake_refresh():
        return None

    from app.services.ai.metadata_index_service import MetadataIndexService

    monkeypatch.setattr(ms.MetadataService, "get_dataset_by_id", fake_get_dataset)
    monkeypatch.setattr(ms.AgentConfigProvider, "refresh_dataset_menu", fake_refresh)
    # 删除链路会用 create_task 异步清向量，测试里换成 AsyncMock，避免遗留任务告警。
    monkeypatch.setattr(
        MetadataIndexService, "delete_dataset_vectors", AsyncMock(return_value=None)
    )

    db = AsyncMock()
    db.execute.return_value = _Result([])

    await ms.MetadataService.delete_dataset(db, 42, user_id=1, user_name="tester")

    statements = [str(call.args[0]) for call in db.execute.call_args_list]
    permission_deletes = [
        stmt for stmt in statements if "ai_agent_resource_permissions" in stmt
    ]
    assert permission_deletes, statements
    # 参数断言：resource_id 必须是字符串形式的 dataset_id
    compiled = db.execute.call_args_list[
        next(i for i, stmt in enumerate(statements) if "ai_agent_resource_permissions" in stmt)
    ].args[0]
    params = compiled.compile().params
    assert "metadata" in params.values()
    assert str(42) in params.values()


# ---------------------------------------------------------------------------
# 智能体工具级绑定优先于用户手选
# ---------------------------------------------------------------------------


def test_bound_dataset_scope_overrides_mounted_scope() -> None:
    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_scope_ids

    ctx = AgentContext(
        agent_id="a1",
        agent_name="助手",
        user_id=1,
        metadata_dataset_ids=["1"],
        bound_metadata_dataset_ids=["2"],
    )
    set_agent_context(ctx)
    try:
        assert resolve_mounted_dataset_scope_ids() == ["2"]
    finally:
        set_agent_context(None)


def test_extract_bound_dataset_ids_supports_dict_and_object() -> None:
    """工具配置可能是 JSON dict，也可能是 ToolConfigItem 对象，两种都要认。"""
    from types import SimpleNamespace

    from app.services.ai.metadata_dataset_scope import extract_bound_metadata_dataset_ids

    assert extract_bound_metadata_dataset_ids(
        [{"name": "get_dataset_schema", "metadata_dataset_ids": ["3"]}]
    ) == ["3"]
    assert extract_bound_metadata_dataset_ids(
        [
            SimpleNamespace(name="other", metadata_dataset_ids=["9"]),
            SimpleNamespace(name="get_dataset_schema", metadata_dataset_ids=[3, "x", "4"]),
        ]
    ) == ["3", "4"]
    assert extract_bound_metadata_dataset_ids(None) == []


def test_bound_dataset_scope_falls_back_when_absent() -> None:
    from app.core.context import AgentContext, set_agent_context
    from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_scope_ids

    ctx = AgentContext(agent_id="a1", agent_name="助手", user_id=1, metadata_dataset_ids=["1"])
    set_agent_context(ctx)
    try:
        assert resolve_mounted_dataset_scope_ids() == ["1"]
    finally:
        set_agent_context(None)

