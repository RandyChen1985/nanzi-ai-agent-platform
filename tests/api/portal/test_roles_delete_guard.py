"""删除角色接口的权限码与系统角色保护（后端）。

背景：权限树里的 `element:role:delete`（「删除角色」）此前是**死码**——
前端从未使用，后端 `DELETE /api/portal/roles/{id}` 校验的是 `element:role:edit`。
结果是管理员勾不勾「删除角色」都没有区别。

这里锁定两点：

1. 删除端点的权限码改成 `element:role:delete`，其余写端点（创建/更新/分配）保持
   `element:role:edit`，避免把删除能力顺带绑死在编辑权限上；
2. 系统角色（default/admin/system/miniprogram_user）一律拒绝删除，且在清理权限/
   成员关系之前就拒绝，否则会出现「关系删了、角色还在」的半残状态。
   `miniprogram_user` 是微信小程序静默登录自动开户时按 code 分配的角色
   （用户反馈「小程序默认角色也不能删除」），删掉会让新用户开户直接失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.dml import Delete

from app.api.portal.endpoints.roles import delete_role
from app.models.permission import Role

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
    """只记录删除端点的副作用，不连库。"""

    def __init__(self, role=None):
        self.role = role
        self.deleted: list = []
        self.committed = False
        self.executed: list = []

    async def get(self, model, pk):
        return self.role

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeResult([])

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


def _delete_endpoint_source() -> str:
    source = ROLES_PY.read_text(encoding="utf-8")
    return source.split("async def delete_role(", 1)[1].split(
        "# --- Role User Management ---", 1
    )[0]


def _update_endpoint_source() -> str:
    source = ROLES_PY.read_text(encoding="utf-8")
    return source.split("async def update_role(", 1)[1].split("async def delete_role(", 1)[0]


def test_delete_endpoint_requires_dedicated_delete_permission():
    """删除角色归 element:role:delete 管，不再借用 element:role:edit。"""
    segment = _delete_endpoint_source()

    assert 'require_permission("element", "element:role:delete")' in segment
    assert 'require_permission("element", "element:role:edit")' not in segment


def test_update_endpoint_keeps_edit_permission():
    """更新端点保持 element:role:edit，避免删除权限被顺手提升为编辑权限。"""
    segment = _update_endpoint_source()

    assert 'require_permission("element", "element:role:edit")' in segment


@pytest.mark.parametrize(
    "code",
    [
        "default", "admin", "system", "miniprogram_user",
        "DEFAULT", " Admin ", "MiniProgram_User",
    ],
)
async def test_system_role_cannot_be_deleted(code: str):
    """系统角色拒绝删除，且不产生任何副作用。"""
    role = Role(id=1, code=code, name="系统角色")
    session = _FakeSession(role=role)

    with pytest.raises(HTTPException) as exc:
        await delete_role(role_id=1, admin={"role": "admin", "user_id": "1"}, db=session)

    assert exc.value.status_code == 400
    assert "系统角色" in exc.value.detail
    assert session.executed == [], "拒绝删除发生在清理权限/关系之前"
    assert session.deleted == []
    assert session.committed is False


async def test_regular_role_still_deletable():
    """普通角色照旧可删，清理顺序保持：先清权限与关系，再删角色。"""
    role = Role(id=9, code="finance_manager", name="财务经理")
    session = _FakeSession(role=role)

    result = await delete_role(role_id=9, admin={"role": "admin", "user_id": "1"}, db=session)

    assert result == {"message": "Role deleted successfully"}
    deletes = [stmt for stmt in session.executed if isinstance(stmt, Delete)]
    assert len(deletes) == 2, "应清理 ResourcePermission 与 UserRoleRelation 两张表"
    assert session.deleted == [role]
    assert session.committed is True


async def test_missing_role_still_404():
    """不存在的角色仍然是 404，不被系统角色保护逻辑吞掉。"""
    session = _FakeSession(role=None)

    with pytest.raises(HTTPException) as exc:
        await delete_role(role_id=404, admin={"role": "admin", "user_id": "1"}, db=session)

    assert exc.value.status_code == 404
