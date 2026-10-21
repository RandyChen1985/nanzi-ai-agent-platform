"""用户维度构造：主部门 + 多部门集合 + 含子部门的集合。

背景：`dept_code` 是**主部门快照**（单值），原先 ChatBI / 固化报表 / 明细导出各自复制了一份
`_chatbi_user_dimensions()`，只塞 `dept_code`。一个用户属于 A、B 两个部门时，行级规则
`dept_code = {user.dept_code}` 只能匹配主部门，B 部门的数据就永远查不到（漏权）。

这里锁住公共构造函数的三件事：
1. `dept_code` 保持主部门（旧规则零改动）；
2. `dept_codes` = 本人所属全部启用部门；
3. `dept_codes_subtree` = 本人所属部门 + 它们的全部子孙（部门负责人看整个分支）。

停用部门必须被排除：停用的语义是"这个部门不再使用"，若仍参与数据权限，
会出现"部门已停用但数据还能被看到"的怪状态。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_infrastructure

USER = {
    "user_id": 42,
    "user_name": "zhangsan",
    "real_name": "张三",
    "role": "user",
    "dept_code": "EAST",
    "org_path": "/1/2/",
    "extra_data": "{}",
}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    """按调用顺序返回预设结果，并记录每次执行的 SQL（用于结构化断言）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.statements = []
        self.rolled_back = False

    async def execute(self, statement):
        self.statements.append(statement)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Result(item)

    async def rollback(self):
        self.rolled_back = True


def _compiled(statement) -> str:
    return str(
        statement.compile(
            dialect=__import__("sqlalchemy").dialects.mysql.dialect(), compile_kwargs={"literal_binds": False}
        )
    ).upper()


@pytest.mark.asyncio
async def test_exposes_primary_and_all_department_codes():
    from app.services.user_dimension_service import build_sql_user_dimensions

    session = _FakeSession(
        [
            [("EAST", "/1/"), ("WEST", "/2/")],          # 本人所属部门
            [("EAST-SH",), ("WEST-BJ",)],                # 这些部门的子孙
        ]
    )
    dims = await build_sql_user_dimensions(session, USER, 42)

    assert dims["dept_code"] == "EAST"          # 主部门，向后兼容
    assert dims["dept_codes"] == ["EAST", "WEST"]
    assert dims["dept_codes_subtree"] == ["EAST", "EAST-SH", "WEST", "WEST-BJ"]
    assert dims["user_name"] == "zhangsan"
    assert dims["id"] == 42


@pytest.mark.asyncio
async def test_disabled_departments_are_excluded():
    from app.services.user_dimension_service import build_sql_user_dimensions

    session = _FakeSession([[("EAST", "/1/")], []])
    await build_sql_user_dimensions(session, USER, 42)

    first = _compiled(session.statements[0])
    assert "STATUS" in first, "本人部门查询必须过滤启用状态"
    second = _compiled(session.statements[1])
    assert "STATUS" in second, "子孙部门查询必须过滤启用状态"


@pytest.mark.asyncio
async def test_user_without_departments_returns_empty_lists_and_skips_subtree_query():
    from app.services.user_dimension_service import build_sql_user_dimensions

    session = _FakeSession([[]])
    dims = await build_sql_user_dimensions(session, USER, 42)

    assert dims["dept_codes"] == []
    assert dims["dept_codes_subtree"] == []
    # 没有任何部门时不该再发一条必然为空的子树查询
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_subtree_lookup_uses_materialized_path_prefix():
    from app.services.user_dimension_service import build_sql_user_dimensions

    session = _FakeSession([[("EAST", "/1/7/")], []])
    await build_sql_user_dimensions(session, USER, 42)

    second = _compiled(session.statements[1])
    assert "LIKE" in second, "子孙查询必须按物化路径前缀匹配"


@pytest.mark.asyncio
async def test_exactly_two_queries_for_multi_department_user():
    """禁止逐部门 N+1：本人部门一条、子孙一条。"""
    from app.services.user_dimension_service import build_sql_user_dimensions

    session = _FakeSession([[("A", "/1/"), ("B", "/2/"), ("C", "/3/")], []])
    await build_sql_user_dimensions(session, USER, 42)

    assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_degrades_to_empty_lists_when_department_tables_are_missing():
    """代码先于迁移上线时不能把 ChatBI 打挂（此时库里也不可能有部门数据）。"""
    from app.services.user_dimension_service import build_sql_user_dimensions

    session = _FakeSession([RuntimeError("Table 'ai_agent_user_departments' doesn't exist")])
    dims = await build_sql_user_dimensions(session, USER, 42)

    assert dims["dept_codes"] == []
    assert dims["dept_codes_subtree"] == []
    assert dims["dept_code"] == "EAST", "降级时主部门维度仍然可用"
    assert session.rolled_back is True, "失败的查询必须回滚，否则同一 session 后续查询全挂"
