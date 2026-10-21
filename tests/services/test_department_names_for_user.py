"""个人中心要能自助看到「我属于哪些部门」。

`/api/portal/auth/me` 早就返回了 `dept_code` 与 `org_path`，但前端一处都没用，
个人中心资料区里也完全没有部门——普通用户（进不了用户管理）根本无从知道自己的
部门归属。而智能体在回答里恰恰会指路"去个人中心 → 我的资料查看"，那里却是空的。

这里把展示用的字段补齐：`dept_name`（主部门名）与 `dept_names`（全部部门名，
主部门排第一）。都不含 id / while 编码路径——给人看的是名字（总部、开发部），
`/1/2/` 与 `/main/dev/` 属于机器格式，不进个人中心。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.department import Department, UserDepartment
from app.models.user import User
from app.services.department_service import DepartmentService

pytestmark = []


@pytest.fixture
async def two_dept_user(db_session):
    """临时造「开发部（主）+ 总部」的用户，测完清理。"""
    suffix = uuid.uuid4().hex[:8]
    svc = DepartmentService(db_session)

    root = await svc.create(name=f"总部{suffix}", code=f"root{suffix}")
    child = await svc.create(name=f"开发部{suffix}", code=f"dev{suffix}", parent_id=int(root.id))

    user = User(
        user_name=f"dept_probe_{suffix}",
        real_name="探针用户",
        role="user",
        status=1,
        # api_key_hash 是 NOT NULL，探针用户给个不可能被用来登录的占位值
        api_key_hash=f"probe{uuid.uuid4().hex}",
    )
    db_session.add(user)
    await db_session.flush()

    db_session.add_all([
        UserDepartment(user_id=int(user.id), dept_id=int(child.id), is_primary=1),
        UserDepartment(user_id=int(user.id), dept_id=int(root.id), is_primary=0),
    ])
    await db_session.flush()
    await svc.sync_primary_snapshot(int(user.id))

    try:
        yield db_session, int(user.id), str(child.name), str(root.name), int(child.id)
    finally:
        # 造的数据都在未提交事务里，回滚即可，不留垃圾
        await db_session.rollback()


@pytest.mark.asyncio
async def test_names_put_primary_department_first(two_dept_user):
    db, user_id, child_name, root_name, _ = two_dept_user

    names = await DepartmentService.department_names_for_user(db, user_id)

    assert names == [child_name, root_name], "主部门必须排第一，兼职部门随后"


@pytest.mark.asyncio
async def test_label_reuses_the_same_names(two_dept_user):
    db, user_id, child_name, root_name, _ = two_dept_user

    label = await DepartmentService.department_label_for_user(db, user_id)

    assert label == f"{child_name}（兼：{root_name}）"


@pytest.mark.asyncio
async def test_primary_snapshot_stores_code_path_not_id_path(two_dept_user):
    db, user_id, _, _, _ = two_dept_user

    snapshot = (await db.execute(
        select(User.org_path, User.dept_code).where(User.id == user_id)
    )).first()

    assert snapshot is not None
    org_path, dept_code = snapshot
    assert org_path and org_path.startswith("/") and not org_path.strip("/").isdigit(), (
        f"org_path 应是编码路径而不是 id 路径，实际 {org_path!r}"
    )
    assert dept_code and dept_code.startswith("dev")


@pytest.mark.asyncio
async def test_user_without_departments_gets_empty_list(db_session):
    """没有部门的用户不能因此报错，前端要能退化成「未设置部门」。"""
    assert await DepartmentService.department_names_for_user(db_session, 999_999_999) == []
