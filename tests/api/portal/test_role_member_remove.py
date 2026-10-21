"""角色成员「只做减法」的移出端点（后端）。

背景见 `docs/superpowers/specs/2026-10-20-role-member-drawer-design.md`：
现有的 `POST /roles/{id}/users` 是**整集替换**，要移出一个人必须先把全量成员 ID
取回来再提交。两个管理员并发操作时会互相覆盖（A 打开时的成员快照会把 B 刚做的
移出撤回）。角色成员抽屉需要「点一下立即移出」，因此新增只做减法的端点。

这里锁定四件事：

1. 只删除目标角色下的关系，绝不触碰这些用户在其他角色里的关系；
2. 幂等——重复提交同一批 user_ids 不报错，`removed` 只统计真正删除的行；
3. 空数组短路，不产生任何 SQL 副作用；
4. 删除后必须让这些用户的权限缓存失效，否则被移出者在 TTL（1 小时）内还带着角色权限。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.dml import Delete

from app.api.portal.endpoints.roles import remove_role_users
from app.models.permission import Role
from app.services.permission_service import PermissionService

pytestmark = pytest.mark.no_infrastructure

ROLES_PY = Path(__file__).resolve().parents[3] / "app/api/portal/endpoints/roles.py"


class _FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    """记录端点副作用，不连库。`existing` 模拟该角色当前的真实成员。"""

    def __init__(self, role=None, existing=()):
        self.role = role
        self.existing = list(existing)
        self.executed: list = []
        self.committed = False

    async def get(self, model, pk):
        return self.role

    async def execute(self, stmt):
        self.executed.append(stmt)
        if isinstance(stmt, Delete):
            return _FakeResult([])
        return _FakeResult(self.existing)

    async def commit(self):
        self.committed = True


def _remove_endpoint_source() -> str:
    source = ROLES_PY.read_text(encoding="utf-8")
    return source.split("async def remove_role_users(", 1)[1].split(
        "# --- Role Resource Permissions ---", 1
    )[0]


def _request(user_ids):
    """按端点入参构造请求对象（延迟导入，避免实现前模块级失败掩盖断言）。"""
    from app.api.portal.endpoints.roles import RemoveRoleUsersRequest

    return RemoveRoleUsersRequest(user_ids=user_ids)


def test_remove_endpoint_requires_edit_permission():
    """移出成员属于角色编辑能力，不能被读权限或用户编辑权限顶替。"""
    segment = _remove_endpoint_source()

    assert 'require_permission("element", "element:role:edit")' in segment


async def test_removes_only_members_that_exist():
    """`removed` 只统计真实成员，`skipped` 报告本来就不在角色里的人。"""
    role = Role(id=7, code="finance", name="财务")
    session = _FakeSession(role=role, existing=[11, 12])

    result = await remove_role_users(
        role_id=7,
        request=_request([11, 12, 99]),
        admin={"role": "admin", "user_id": "1"},
        db=session,
    )

    assert result["removed"] == 2
    assert result["skipped"] == 1
    assert result["user_ids"] == [11, 12]
    assert session.committed is True


async def test_remove_is_idempotent():
    """再次移除同一批成员：不报错，removed 归零，skipped 记全量。"""
    role = Role(id=7, code="finance", name="财务")
    session = _FakeSession(role=role, existing=[])

    result = await remove_role_users(
        role_id=7,
        request=_request([11, 12]),
        admin={"role": "admin", "user_id": "1"},
        db=session,
    )

    assert result == {"removed": 0, "skipped": 2, "user_ids": []}


async def test_remove_empty_payload_has_no_side_effects():
    """空数组直接返回，不发 SQL、不 commit。"""
    role = Role(id=7, code="finance", name="财务")
    session = _FakeSession(role=role, existing=[11])

    result = await remove_role_users(
        role_id=7,
        request=_request([]),
        admin={"role": "admin", "user_id": "1"},
        db=session,
    )

    assert result == {"removed": 0, "skipped": 0, "user_ids": []}
    assert session.executed == []
    assert session.committed is False


async def test_remove_scopes_delete_to_target_role():
    """删除语句必须同时带 role_id 与 user_id 条件，不能只按 user 删。"""
    role = Role(id=7, code="finance", name="财务")
    session = _FakeSession(role=role, existing=[11])

    await remove_role_users(
        role_id=7,
        request=_request([11]),
        admin={"role": "admin", "user_id": "1"},
        db=session,
    )

    deletes = [stmt for stmt in session.executed if isinstance(stmt, Delete)]
    assert len(deletes) == 1, "一次批量删除即可，不要逐人发 DELETE"
    sql = str(deletes[0])
    assert "role_id" in sql and "user_id" in sql


async def test_remove_invalidates_permission_cache(monkeypatch):
    """被移出者必须立刻掉权限，不能等缓存 TTL 过期。"""
    role = Role(id=7, code="finance", name="财务")
    session = _FakeSession(role=role, existing=[11, 12])
    calls: list[list[int]] = []

    async def _fake_invalidate(self, user_ids):
        calls.append(sorted(int(uid) for uid in user_ids))

    monkeypatch.setattr(
        PermissionService, "invalidate_cached_permissions_for_users", _fake_invalidate
    )

    await remove_role_users(
        role_id=7,
        request=_request([11, 12]),
        admin={"role": "admin", "user_id": "1"},
        db=session,
    )

    assert calls == [[11, 12]]


async def test_remove_missing_role_returns_404():
    """角色被并发删除时给 404，而不是静默返回 0。"""
    session = _FakeSession(role=None)

    with pytest.raises(HTTPException) as exc:
        await remove_role_users(
            role_id=404,
            request=_request([11]),
            admin={"role": "admin", "user_id": "1"},
            db=session,
        )

    assert exc.value.status_code == 404
    assert session.committed is False
