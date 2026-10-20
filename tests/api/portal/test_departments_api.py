"""部门接口契约：权限口径、错误语义、成员增量语义。

权限口径与角色成员一致：读（树 / 成员）放宽到 `menu:system:departments` 或
`element:dept:edit`，让只读管理员能看；写操作严格要 `element:dept:edit`，
删除部门另要 `element:dept:delete`。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints.departments import (
    add_department_members,
    delete_department,
    get_department_tree,
    remove_department_members,
)

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
DEPARTMENTS_PY = ROOT / "app/api/portal/endpoints/departments.py"
API_PY = ROOT / "app/api/portal/api.py"


def _source() -> str:
    return DEPARTMENTS_PY.read_text(encoding="utf-8")


def test_router_registered_in_portal_api():
    assert "departments.router" in API_PY.read_text(encoding="utf-8")
    assert 'prefix="/departments"' in API_PY.read_text(encoding="utf-8")


def test_tree_read_permission_is_relaxed_for_readonly_admins():
    """读权限必须同时接受菜单码与编辑码，否则只读管理员点开就 403。"""
    src = _source()
    assert "READ_PERMISSION = require_any_permission(" in src
    assert '"menu", "menu:system:departments"' in src
    assert '"element", "element:dept:edit"' in src


def test_write_endpoints_require_dept_edit_permission():
    src = _source()
    assert 'require_permission("element", "element:dept:edit")' in src
    # 创建 / 更新 / 排序 / 成员增删都必须带这个依赖
    assert src.count('require_permission("element", "element:dept:edit")') >= 5


def test_delete_endpoint_requires_dept_delete_permission():
    src = _source()
    segment = src.split('@router.delete("/{dept_ref}"', 1)[1].split("@router.", 1)[0]
    assert 'require_permission("element", "element:dept:delete")' in segment


def test_members_use_incremental_endpoints_not_replace():
    """成员是增量语义：不提供整集替换接口，避免"提交全集"式覆盖。"""
    src = _source()
    assert '@router.post("/{dept_ref}/members")' in src
    assert '@router.post("/{dept_ref}/members/remove")' in src
    assert "service.remove_members(" in src
    service_src = (ROOT / "app/services/department_service.py").read_text(encoding="utf-8")
    assert '"removed"' in service_src and '"skipped"' in service_src


class _FakeDept:
    id = 7


class _CountSession:
    def __init__(self, child_count: int = 0, member_count: int = 0):
        self._counts = [child_count, member_count]

    async def get(self, model, pk):
        return _FakeDept()

    async def execute(self, stmt):
        value = self._counts.pop(0) if self._counts else 0

        class _R:
            def scalar(_self):
                return value

            def scalars(_self):
                return _self

            def all(_self):
                return []

        return _R()

    async def flush(self):
        return None

    async def delete(self, obj):
        return None

    async def commit(self):
        return None


async def test_delete_returns_409_when_department_has_children():
    with pytest.raises(HTTPException) as exc:
        await delete_department(
            7, admin={"role": "admin"}, db=_CountSession(child_count=2)
        )
    assert exc.value.status_code == 409
    assert "子部门" in exc.value.detail


async def test_delete_returns_409_when_department_has_members():
    with pytest.raises(HTTPException) as exc:
        await delete_department(
            7, admin={"role": "admin"}, db=_CountSession(child_count=0, member_count=3)
        )
    assert exc.value.status_code == 409
    assert "成员" in exc.value.detail


async def test_delete_returns_404_when_department_missing():
    class _Missing(_CountSession):
        async def get(self, model, pk):
            return None

    with pytest.raises(HTTPException) as exc:
        await delete_department(99, admin={"role": "admin"}, db=_Missing())
    assert exc.value.status_code == 404
