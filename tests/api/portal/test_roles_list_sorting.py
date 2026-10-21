"""角色列表「按用户数排序」的后端契约。

角色列表是服务端分页（page/size），所以排序必须落在 SQL 的 ORDER BY 上：前端
本地排序只能排当前页，用户数最多的角色一旦不在第一页就永远排不上来。这里锁定：

1. `sort_by` / `sort_order` 只接受白名单，未知值不会被拼进 ORDER BY；
2. 用户数排序用的是 join 用户表后的计数（与列表 `user_count` 同口径），
   不是关系行数；
3. 用户数相同的角色有稳定兜底次序（创建时间/ID），翻页不重复、不漏行；
4. 默认排序保持原样（创建时间倒序），避免本次改动悄悄改变现有列表顺序。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.sql.selectable import Select

from app.api.portal.endpoints.roles import list_roles
from app.models.permission import Role

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
ROLES_PY = ROOT / "app/api/portal/endpoints/roles.py"


class _Result:
    def __init__(self, rows=(), scalar_value=None):
        self._rows = list(rows)
        self._scalar = scalar_value

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _Session:
    """只按顺序回放两条查询：计数、分页列表。"""

    def __init__(self, role_rows):
        self._responses = [
            _Result([], scalar_value=len(role_rows)),
            _Result(role_rows),
        ]
        self.executed: list = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self._responses.pop(0)


def _order_by_clause(sql: str) -> str:
    assert "ORDER BY" in sql.upper(), f"分页列表必须带 ORDER BY，实际 SQL：{sql}"
    return sql.upper().split("ORDER BY", 1)[1]


async def _page_sql(sort_by: str, sort_order: str) -> str:
    rows = [
        (Role(id=1, code="ops", name="运营"), 9),
        (Role(id=2, code="viewer", name="访客"), 0),
    ]
    session = _Session(rows)

    result = await list_roles(
        page=2,
        size=20,
        search=None,
        sort_by=sort_by,
        sort_order=sort_order,
        admin={"role": "admin"},
        db=session,
    )

    assert result["total"] == 2
    assert [item["user_count"] for item in result["items"]] == [9, 0]

    page_stmt = session.executed[-1]
    assert isinstance(page_stmt, Select)
    # 只有两条查询：计数 + 带用户数的分页列表（不再逐角色回查）
    assert len(session.executed) == 2
    # literal_binds 便于断言系统角色 code 白名单，不依赖 POSTCOMPILE 占位符
    return str(
        page_stmt.compile(
            dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_sort_params_are_whitelisted():
    """排序字段/方向必须是白名单，不能让调用方把任意列名塞进 ORDER BY。"""
    params = inspect.signature(list_roles).parameters
    assert params["sort_by"].default.default == "created_at"
    assert params["sort_order"].default.default == "desc"

    source = ROLES_PY.read_text(encoding="utf-8")
    segment = source.split("async def list_roles(", 1)[1].split("async def create_role(", 1)[0]
    assert 'pattern="^(created_at|user_count)$"' in segment, (
        "sort_by 必须限定在已知列上，否则可被用来探测/排序任意字段"
    )
    assert 'pattern="^(asc|desc)$"' in segment, "sort_order 只允许 asc/desc"


async def test_default_order_stays_created_at_desc():
    """默认（不传排序参数）仍是创建时间倒序，但系统角色照样置顶。"""
    sql = await _page_sql("created_at", "desc")
    clause = _order_by_clause(sql).lstrip()

    assert clause.startswith("CASE WHEN"), "系统角色置顶是恒定规则，与所选排序列无关"
    assert "COALESCE" not in clause
    assert clause.index("END DESC") < clause.index("AI_AGENT_ROLES.CREATED_AT DESC")


async def test_system_roles_are_always_pinned_first():
    """系统角色固定置顶：升降序都排在最前面，组内才应用用户数排序。

    default/admin 出错会直接影响登录与开户，不能因为按用户数排序被挤到后面几页；
    判定口径与删除保护一致（去空白 + 小写归一，避免 `Admin` 这类历史值漏判）。
    """
    for order in ("asc", "desc"):
        sql = await _page_sql("user_count", order)
        clause = _order_by_clause(sql).lstrip()

        assert clause.startswith("CASE WHEN"), "系统角色标记必须是第一排序项"
        assert "LOWER(TRIM(AI_AGENT_ROLES.CODE))" in clause, (
            "系统角色判定要按去空白+小写归一，和删除保护同口径"
        )
        for code in ("admin", "default", "miniprogram_user", "system"):
            assert f"'{code.upper()}'" in clause, f"系统角色判定缺少 {code}"

        # 系统角色标记 DESC（1 在前）之后才是用户数排序
        assert clause.index("END DESC") < clause.index("COALESCE")


async def test_user_count_desc_orders_by_joined_count_in_sql():
    """倒序：组内 ORDER BY 用 join 用户表后的计数，且排序发生在 LIMIT 之前。"""
    sql = await _page_sql("user_count", "desc")
    clause = _order_by_clause(sql).lstrip()

    assert "COALESCE" in clause, "无成员角色按 0 参与排序，不能出现 NULL 排序差异"
    assert "0) DESC," in clause, "系统角色组内按用户数倒序"
    # 排序值来自 join 用户表的分组子查询（不是只数关系行）
    assert "COUNT(AI_AGENT_USERS.ID)" in sql.upper()
    assert "GROUP BY" in sql.upper()
    assert "LIMIT" in sql.upper(), "排序必须与分页在同一条 SQL 里，否则只排了当前页"


async def test_user_count_asc_flips_direction_but_keeps_stable_tiebreaker():
    """升序：组内方向翻转，用户数相同时仍按创建时间/ID 兜底。"""
    sql = await _page_sql("user_count", "asc")
    clause = _order_by_clause(sql).lstrip()

    # 系统角色标记之后是用户数表达式，且组内方向为 ASC
    assert clause.startswith("CASE WHEN")
    assert "0) ASC," in clause
    assert clause.index("COALESCE") < clause.index("AI_AGENT_ROLES.CREATED_AT DESC")

    # 同用户数时用创建时间/ID 稳定次序，保证翻页不重复、不漏行
    assert "AI_AGENT_ROLES.CREATED_AT DESC" in clause
    assert "AI_AGENT_ROLES.ID DESC" in clause
