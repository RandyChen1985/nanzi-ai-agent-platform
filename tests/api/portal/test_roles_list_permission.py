"""「读角色列表」的权限放宽（后端）。

编辑用户时要给用户分配业务角色，但 `GET /api/portal/roles` 原先只认
`menu:system:roles`：只有「用户管理」权限的账号打开编辑弹窗时，角色列表请求 403，
前端又只 `console.error`，界面就成了空网格——用户反馈「角色管理里明明有 default，
编辑用户却搜不到」正是这个组合。

放宽范围**只限读取**：角色增删改仍然各自由 `element:role:edit` / `element:role:delete`
严格把关，避免为了让弹窗能选角色而顺手放开了写权限。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.core.dependencies import require_any_permission

pytestmark = pytest.mark.no_infrastructure

ROLES_PY = Path(__file__).resolve().parents[3] / "app/api/portal/endpoints/roles.py"

READ_GRANTS = ("menu:system:roles", "element:user:edit")


class _FakePermissionService:
    """只按「被授予的 (type, id) 集合」回答，不连库。"""

    granted: set[tuple[str, str]] = set()

    def __init__(self, db):
        self.db = db

    async def check_permission(self, user_id, resource_type, resource_id):
        return (resource_type, resource_id) in _FakePermissionService.granted


@pytest.fixture
def grant(monkeypatch):
    def _set(items):
        _FakePermissionService.granted = {(t, r) for t, r in items}
        monkeypatch.setattr(
            "app.services.permission_service.PermissionService", _FakePermissionService
        )

    return _set


def _list_endpoint_source() -> str:
    source = ROLES_PY.read_text(encoding="utf-8")
    return source.split("async def list_roles(", 1)[1].split("async def create_role(", 1)[0]


def test_list_endpoint_accepts_role_management_or_user_edit_permission():
    """读列表：角色管理查看权限或用户编辑权限任一即可（新建用户同样由 element:user:edit 把关）。"""
    segment = _list_endpoint_source()

    assert "require_any_permission" in segment, "读列表仍在用单一权限依赖"
    for perm in READ_GRANTS:
        assert f'"{perm}"' in segment, f"读角色列表没有接受 {perm}"


def test_write_endpoints_keep_strict_single_permission():
    """写操作不得跟着放宽：创建角色仍只认 element:role:edit。"""
    source = ROLES_PY.read_text(encoding="utf-8")
    create_segment = source.split("async def create_role(", 1)[1].split(
        "async def update_role(", 1
    )[0]

    assert 'require_permission("element", "element:role:edit")' in create_segment
    assert "require_any_permission" not in create_segment, "写操作不能用「任一权限」放宽"


async def test_admin_bypasses_without_any_grant(grant):
    """admin 不需要任何一条也放行。"""
    grant([])
    dep = require_any_permission(("menu", "menu:system:roles"), ("element", "element:user:edit"))
    user = {"role": "admin", "user_id": "1"}

    assert await dep(user=user, db=object()) is user


@pytest.mark.parametrize("granted_perm", [("menu", "menu:system:roles"), ("element", "element:user:edit")])
async def test_any_single_grant_is_enough(grant, granted_perm):
    """任一权限命中即可，不要求同时拥有。"""
    grant([granted_perm])
    dep = require_any_permission(("menu", "menu:system:roles"), ("element", "element:user:edit"))
    user = {"role": "user", "user_id": "7"}

    assert await dep(user=user, db=object()) is user


async def test_missing_all_grants_raises_403_listing_required_permissions(grant):
    """一条都没有时 403，且 detail 要说清缺哪些权限码（便于排障）。"""
    grant([])
    dep = require_any_permission(("menu", "menu:system:roles"), ("element", "element:user:edit"))

    with pytest.raises(HTTPException) as exc:
        await dep(user={"role": "user", "user_id": "7"}, db=object())

    assert exc.value.status_code == 403
    assert "menu:system:roles" in exc.value.detail
    assert "element:user:edit" in exc.value.detail
