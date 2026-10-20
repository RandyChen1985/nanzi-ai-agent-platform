"""角色成员列表：读权限口径与成员行字段（后端）。

角色成员抽屉要展示"这个人还属于哪些其他角色"。多角色权限是并集，缺这一列会让
管理员把"在此角色里的权限"误当成此人的全部权限，进而误判该不该移出。

同时修一个既有缺口：`GET /roles/{id}/users` 只认 `element:role:edit`，但角色列表
允许 `menu:system:roles` 进入——只读管理员能进页面、看得到用户数，点开却 403。
读放开、写收紧，与 `list_roles` 的既有口径一致。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.api.portal.endpoints.roles import get_role_users
from app.models.permission import Role
from app.models.user import User

pytestmark = pytest.mark.no_infrastructure

ROLES_PY = Path(__file__).resolve().parents[3] / "app/api/portal/endpoints/roles.py"


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


class _ScriptedSession:
    """按调用顺序吐结果，用来断言端点没有逐行查询。"""

    def __init__(self, role, responses):
        self.role = role
        self._responses = list(responses)
        self.calls = 0

    async def get(self, model, pk):
        return self.role

    async def execute(self, stmt):
        self.calls += 1
        if not self._responses:
            raise AssertionError(f"意料之外的额外查询（第 {self.calls} 次）：{stmt}")
        return self._responses.pop(0)


def _user(uid: int, name: str) -> User:
    user = User(id=uid, user_name=name, real_name=name, status=1)
    user.email = f"{name}@example.com"
    user.last_login_at = datetime(2026, 10, 19, 8, 30)
    user.role = "user"
    return user


def _get_users_source() -> str:
    source = ROLES_PY.read_text(encoding="utf-8")
    return source.split("async def get_role_users(", 1)[1].split(
        '@router.post("/{role_id}/users")', 1
    )[0]


def test_read_members_allows_role_page_viewers():
    """只读管理员（只有菜单权限）也能看成员列表。"""
    segment = _get_users_source()

    assert "require_any_permission(" in segment
    assert '("menu", "menu:system:roles")' in segment
    assert '("element", "element:role:edit")' in segment


def test_read_members_does_not_require_edit_permission_alone():
    """不能退化成仅 element:role:edit，否则只读管理员仍是 403。"""
    segment = _get_users_source()

    assert 'admin: dict = Depends(require_permission("element", "element:role:edit"))' not in segment


async def test_member_items_carry_identity_and_role_names():
    """成员行带上邮箱、最后登录、系统角色，以及全部业务角色名。"""
    role = Role(id=7, code="finance", name="财务")
    session = _ScriptedSession(
        role=role,
        responses=[
            _Result([1, 2]),                    # 该角色全量成员 ID
            _Result([], scalar_value=2),         # 分页 total
            _Result([_user(1, "alice"), _user(2, "bob")]),  # 当前页成员
            _Result([(2, "默认角色"), (2, "审计员")]),        # 批量取角色名
        ],
    )

    payload = await get_role_users(
        role_id=7, page=1, size=50, search=None, admin={"role": "admin"}, db=session
    )

    assert payload["user_ids"] == [1, 2]
    first, second = payload["items"]
    assert first["email"] == "alice@example.com"
    assert first["role"] == "user"
    assert first["last_login_at"] == "2026-10-19T08:30:00"
    assert first["role_names"] == []
    assert second["role_names"] == ["默认角色", "审计员"]


async def test_role_names_are_fetched_in_one_query():
    """单页 50 人不能变成 50 次查询：总查询次数固定为 4（成员ID/计数/成员页/角色名）。"""
    role = Role(id=7, code="finance", name="财务")
    session = _ScriptedSession(
        role=role,
        responses=[
            _Result([1]),
            _Result([], scalar_value=1),
            _Result([_user(1, "alice")]),
            _Result([]),
        ],
    )

    await get_role_users(
        role_id=7, page=1, size=50, search=None, admin={"role": "admin"}, db=session
    )

    assert session.calls == 4
