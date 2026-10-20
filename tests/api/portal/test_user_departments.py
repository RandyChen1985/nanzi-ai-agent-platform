"""用户侧的部门归属：整集保存、列表过滤与字段、删除清理。

这三件事分属不同语义，必须分清：

1. **用户侧保存是整集替换**（`PUT /users/{id}/departments`）——部门是用户属性，
   弹窗里就是"最终提交这套部门"，与角色成员那种增量场景不同；
2. **列表过滤**要支持"含子部门"，靠物化路径前缀匹配，不递归；
3. **删除用户必须显式清理部门关系**——两张表都没有外键（用户决策），漏删就是悬挂数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints.management import (
    SetUserDepartmentsRequest,
    set_user_departments,
)

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MANAGEMENT_PY = ROOT / "app/api/portal/endpoints/management.py"


def _source() -> str:
    return MANAGEMENT_PY.read_text(encoding="utf-8")


def test_set_departments_requires_user_edit_permission():
    src = _source()
    segment = src.split("async def set_user_departments(", 1)[1].split("async def ", 1)[0]
    assert 'require_permission("element", "element:user:edit")' in segment


def test_set_departments_goes_through_department_service():
    """快照同步只能在服务里做，接口层不得直接写 User.dept_code。"""
    src = _source()
    segment = src.split("async def set_user_departments(", 1)[1].split("async def ", 1)[0]
    assert "DepartmentService" in segment
    assert "set_user_departments(" in segment
    assert "user.dept_code" not in segment


def test_list_users_accepts_department_filters():
    src = _source()
    segment = src.split("async def list_users(", 1)[1].split("@router.", 1)[0]
    assert "dept_id: Optional[int] = Query(" in segment
    assert "include_sub: bool = Query(" in segment
    assert "subtree_like_prefix" in segment


def test_list_users_returns_department_fields():
    src = _source()
    for field in ("dept_ids", "dept_names", "primary_dept_id", "primary_dept_name"):
        assert f'"{field}"' in src, f"items 缺少 {field}"


def test_list_users_department_names_use_single_batch_query():
    """本页用户的部门信息必须一次 IN 查询取回，禁止逐行查（N+1）。"""
    src = _source()
    assert "UserDepartment.user_id.in_(page_user_ids)" in src


def test_delete_user_removes_department_relations():
    """无外键：删除用户必须显式清理部门关系。"""
    src = _source()
    segment = src.split("async def delete_user(", 1)[1].split("@router.post(", 1)[0]
    assert "delete(UserDepartment).where(UserDepartment.user_id == user_id)" in segment


class _FakeUser:
    id = 12


class _UserSession:
    def __init__(self):
        self.committed = False

    async def get(self, model, pk):
        return _FakeUser()

    async def commit(self):
        self.committed = True


async def test_set_departments_rejects_primary_outside_selection():
    """主部门必须在已选集合里，否则会写出"主部门存在但用户不是成员"的非法状态。"""
    with pytest.raises(HTTPException) as exc:
        await set_user_departments(
            12,
            request=SetUserDepartmentsRequest(dept_ids=[1, 2], primary_dept_id=9),
            admin={"role": "admin"},
            db=_UserSession(),
        )
    assert exc.value.status_code == 400
    assert "主部门" in exc.value.detail


async def test_set_departments_returns_404_for_missing_user():
    class _Missing(_UserSession):
        async def get(self, model, pk):
            return None

    with pytest.raises(HTTPException) as exc:
        await set_user_departments(
            999,
            request=SetUserDepartmentsRequest(dept_ids=[], primary_dept_id=None),
            admin={"role": "admin"},
            db=_Missing(),
        )
    assert exc.value.status_code == 404


def test_list_users_supports_exclude_dept_id_for_member_picker():
    """部门「添加成员」的候选列表由后端排除本部门成员，避免前端拿不全成员 id。"""
    src = _source()
    segment = src.split("async def list_users(", 1)[1].split("@router.", 1)[0]
    assert "exclude_dept_id: Optional[int] = Query(" in segment
    assert "~User.id.in_(dept_member_ids)" in segment


def test_direct_snapshot_write_is_rejected_on_update():
    """dept_code / org_path 只能由 DepartmentService 派生：接口层收到就 400 指路。

    静默忽略更糟——调用方会以为写成功了，而库里其实没变。
    """
    segment = _source().split("async def update_user(", 1)[1].split("@router.", 1)[0]
    assert "dept_code / org_path 已由主部门自动派生" in segment
    assert "PUT /management/users/{user_id}/departments" in segment


def test_direct_snapshot_write_is_rejected_on_create():
    segment = _source().split("async def create_user(", 1)[1].split("@router.", 1)[0]
    assert "dept_code / org_path 已由主部门自动派生" in segment


def test_no_endpoint_writes_snapshot_columns_directly():
    src = _source()
    assert "user.dept_code = request.dept_code" not in src
    assert "user.org_path = request.org_path" not in src


def test_three_query_endpoints_share_one_dimension_builder():
    """ChatBI / 固化报表 / 明细导出不得再各自复制一份维度构造。

    复制三份的代价是：加一个维度要改三处，漏一处就出现「ChatBI 能按多部门过滤、
    固化报表不能」这种最难查的不一致。
    """
    root = Path(__file__).resolve().parents[3]
    for rel in (
        "app/api/v1/endpoints/chatbi.py",
        "app/api/portal/endpoints/saved_reports.py",
        "app/api/portal/endpoints/chatbi_export.py",
    ):
        src = (root / rel).read_text(encoding="utf-8")
        assert "build_sql_user_dimensions" in src, f"{rel} 未接入公共维度构造"
        assert "def _chatbi_user_dimensions(" not in src, f"{rel} 仍有本地复制实现"
        assert "def _user_dimensions(" not in src, f"{rel} 仍有本地复制实现"


# ---------------------------------------------------------------- 用编码提交归属

class _CodeLookupSession:
    """按编码查部门：codes 里给了的才返回部门对象。"""

    def __init__(self, known: dict[str, int]):
        self.known = known
        self.committed = False

    async def get(self, model, pk):
        return _FakeUser()

    async def execute(self, stmt):
        target = self

        class _R:
            def scalar_one_or_none(_self):
                # 服务层一次只按一个编码查，这里无从得知具体编码，
                # 由调用方通过 known 决定是否存在
                return None

        return _R()

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_rejects_mixing_dept_ids_and_dept_codes():
    """两种标识同时给会有歧义（合并？覆盖？），直接拒绝比猜更安全。"""
    with pytest.raises(HTTPException) as exc:
        await set_user_departments(
            12,
            request=SetUserDepartmentsRequest(dept_ids=[1], dept_codes=["EAST"]),
            admin={"role": "admin"},
            db=_CodeLookupSession({}),
        )
    assert exc.value.status_code == 400
    assert "只能给一种" in exc.value.detail


@pytest.mark.asyncio
async def test_unknown_dept_code_is_rejected():
    """外部系统迁移时写错编码，必须明确报错而不是静默丢弃该部门。"""
    with pytest.raises(HTTPException) as exc:
        await set_user_departments(
            12,
            request=SetUserDepartmentsRequest(dept_codes=["NOPE"]),
            admin={"role": "admin"},
            db=_CodeLookupSession({}),
        )
    assert exc.value.status_code == 400
    assert "NOPE" in exc.value.detail
