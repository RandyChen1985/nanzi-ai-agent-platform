"""禁用资源 review 修复项的后端契约测试（F1 / F2 / F5 / F6 / F10）。

这些修复的共同点是"看不见的边界"：字段未提交时不该重建记录、缓存键要随新字段升版、
用户页回显必须用直配项而不是并集。因此测试刻意打在**删除语句的实际编译结果**与
**实际插入的记录**上，而不是只做字符串检查。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import mysql

from app.schemas.permission import PermissionSet, PermissionUpdate
from app.services.permission_service import PermissionService


pytestmark = pytest.mark.no_infrastructure


class _FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """只够跑 update_user_permissions：记录 execute 语句与 add_all 的记录。"""

    def __init__(self, results=None):
        self.statements = []
        self.added = []
        self.committed = False
        self._results = list(results or [])

    async def execute(self, stmt):
        self.statements.append(stmt)
        if self._results:
            return self._results.pop(0)
        return _FakeResult([])

    def add_all(self, items):
        self.added.extend(items)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


def _compiled_delete_sql(session: _FakeSession) -> str:
    """把 delete(...) 语句编译成字面量 SQL，便于断言删除范围。"""
    deletes = [s for s in session.statements if s.__class__.__name__.startswith("Delete")]
    assert deletes, "update_user_permissions 应当先执行一条 delete"
    return str(deletes[0].compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))


# ---------------------------------------------------------------- F5


@pytest.mark.asyncio
async def test_missing_forbidden_fields_do_not_rebuild_forbidden_records(monkeypatch):
    """旧客户端不提交 forbidden_* 时，绝不能删掉已有禁用项（少了就等于放开）。"""
    session = _FakeSession()
    service = PermissionService(session)
    monkeypatch.setattr(service, "_invalidate_user_cache", lambda *a, **k: _async_none(), raising=False)

    updates = PermissionUpdate(agents=["a1"], menus=["m1"])
    assert "forbidden_skills" not in updates.model_fields_set

    await service.update_user_permissions(7, updates)

    sql = _compiled_delete_sql(session)
    assert "forbidden_skill" not in sql
    assert "forbidden_tool" not in sql
    assert "forbidden_command" not in sql
    assert "model" not in sql.split("IN (")[1], "models 未提交时同样不该进入删除范围"
    assert {"agent", "menu"} <= set(
        part.strip().strip("'") for part in sql.split("IN (")[1].split(")")[0].split(",")
    )


@pytest.mark.asyncio
async def test_explicit_forbidden_fields_still_rebuild_only_those_types(monkeypatch):
    """显式提交了禁用项时照旧重建（该类型必须在删除范围内），否则撤不掉。"""
    session = _FakeSession()
    service = PermissionService(session)
    monkeypatch.setattr(service, "_invalidate_user_cache", lambda *a, **k: _async_none(), raising=False)

    await service.update_user_permissions(
        7, PermissionUpdate(forbidden_skills=["docx"])
    )

    sql = _compiled_delete_sql(session)
    assert "forbidden_skill" in sql
    assert "forbidden_tool" not in sql, "只提交技能时不该顺带清空禁用工具"


async def _async_none():
    return None


# ---------------------------------------------------------------- F6


@pytest.mark.asyncio
async def test_permission_cache_key_is_bumped_for_forbidden_skills():
    """缓存键必须随权限结构变化升版，否则旧缓存会按旧口径继续生效。

    沿革：v4 加 models、v5 加 forbidden_skills、v6 加智能体作用域、
    v7 修复「menu:* 被写成 element」的读取口径（旧缓存里 elements 混着菜单 id）。
    """
    service = PermissionService(None)
    key = await service._get_cache_key(42)
    assert key == "sys:auth:permissions:v7:user:42:agent:-"


# ---------------------------------------------------------------- F2


def test_forbidden_entry_accepts_tool_name_length_but_rejects_absurd():
    """工具名可达 255：上限必须容得下，否则一个长名会让整页权限保存 422。"""
    long_name = "m_" + "x" * 253
    assert len(long_name) == 255
    assert PermissionSet(forbidden_tools=[long_name]).forbidden_tools == [long_name]

    with pytest.raises(ValidationError):
        PermissionSet(forbidden_tools=["y" * 256])


# ---------------------------------------------------------------- F1


@pytest.mark.asyncio
async def test_direct_permissions_query_is_user_scoped_only():
    """直配查询只能按 user_id 取，绝不能带上角色 join（否则又变回并集）。"""
    session = _FakeSession()
    service = PermissionService(session)

    await service.get_user_direct_permissions(9)

    sql = str(
        session.statements[0].compile(
            dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "user_id = 9" in sql
    # role_id 只是被 select 出来的列（select(ResourcePermission) 会带全部列），
    # 关键是**没有 join 角色关系表**——那才是并集回显的来源。
    assert "ai_agent_user_role_relations" not in sql
    assert "JOIN" not in sql.upper()


@pytest.mark.asyncio
async def test_saving_user_page_does_not_persist_role_derived_forbidden_items(monkeypatch):
    """旧前端整份提交并集时，角色级禁用不得被固化成用户级记录（否则角色侧取消也撤不掉）。"""
    session = _FakeSession()
    service = PermissionService(session)
    monkeypatch.setattr(service, "_invalidate_user_cache", lambda *a, **k: _async_none(), raising=False)

    async def _derived(user_id, resource_type):
        return {"docx"} if resource_type == "forbidden_skill" else set()

    monkeypatch.setattr(service, "_role_derived_forbidden_ids", _derived)

    await service.update_user_permissions(
        5,
        PermissionUpdate(forbidden_skills=["docx", "pdf"]),
    )

    persisted = {(item.resource_type, item.resource_id) for item in session.added}
    assert ("forbidden_skill", "pdf") in persisted, "用户自己勾的技能必须落库"
    assert ("forbidden_skill", "docx") not in persisted, "角色已禁的项不该被复制成用户级记录"


def test_get_user_permissions_endpoint_returns_direct_scope():
    """接口层必须把直配 scope 带给前端，否则前端只能继续拿并集回写。"""
    from pathlib import Path

    source = Path("app/api/portal/endpoints/management.py").read_text(encoding="utf-8")
    assert "response.direct_permissions = await service.get_user_direct_permissions(user_id)" in source


# ---------------------------------------------------------------- F10


def test_forbidden_catalog_requires_element_permission():
    """候选目录不能只要求登录：否则任意用户都能枚举工具 / MCP / 技能清单。"""
    from pathlib import Path

    source = Path("app/api/portal/endpoints/tools.py").read_text(encoding="utf-8")
    start = source.index('@router.get("/forbidden-catalog"')
    signature = source[start : source.index("):", start)]
    assert 'require_permission("element", "element:user:edit")' in signature
    assert "require_api_key" not in signature
