"""转移归属前要能看出「这人接手了也管不了」。

转移端点只校验目标账号存在且启用，但改元数据 / 再转移 / 分配授权还有一道
`element:knowledge:edit` 功能点闸门。转给没有该权限点的人，他会成为归属人却
打不开任何管理入口 —— 库变成「有主但管不了」，而原归属人已经失去管理权。

前端据此在候选行上打「无编辑权限」标记，所以判定的三条来源必须一次算准：
系统/业务 admin 角色、用户直配、业务角色继承。这里同时锁住「禁用行不算」。
"""

import uuid

import pytest

from app.models.permission import ResourcePermission, Role, UserRoleRelation
from app.models.user import User
from app.services.permission_service import PermissionService

pytestmark = pytest.mark.asyncio

CODE = "element:knowledge:edit"


async def _make_user(db_session, mark: str, role: str = "user") -> User:
    user = User(
        user_name=f"oc-{mark}-{uuid.uuid4().hex[:6]}",
        role=role,
        status=1,
        api_key_hash=f"hash_{uuid.uuid4().hex}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _cleanup(db_session, mark: str) -> None:
    from sqlalchemy import delete, select

    user_ids = (
        (
            await db_session.execute(
                select(User.id).where(User.user_name.like(f"oc-{mark}-%"))
            )
        )
        .scalars()
        .all()
    )
    role_ids = (
        (await db_session.execute(select(Role.id).where(Role.code.like(f"oc-{mark}-%"))))
        .scalars()
        .all()
    )
    if user_ids:
        await db_session.execute(
            delete(ResourcePermission).where(ResourcePermission.user_id.in_(user_ids))
        )
        await db_session.execute(
            delete(UserRoleRelation).where(UserRoleRelation.user_id.in_(user_ids))
        )
    if role_ids:
        await db_session.execute(
            delete(ResourcePermission).where(ResourcePermission.role_id.in_(role_ids))
        )
    if user_ids:
        await db_session.execute(delete(User).where(User.id.in_(user_ids)))
    if role_ids:
        await db_session.execute(delete(Role).where(Role.id.in_(role_ids)))
    await db_session.flush()


async def test_flag_covers_admin_direct_and_role_paths(db_session):
    mark = uuid.uuid4().hex[:8]
    try:
        admin = await _make_user(db_session, mark, role="admin")
        direct = await _make_user(db_session, mark)
        inherited = await _make_user(db_session, mark)
        nobody = await _make_user(db_session, mark)

        role = Role(code=f"oc-{mark}-kb", name="候选权限测试角色")
        db_session.add(role)
        await db_session.flush()
        db_session.add(UserRoleRelation(user_id=inherited.id, role_id=role.id))
        db_session.add_all(
            [
                ResourcePermission(
                    user_id=direct.id, resource_type="element", resource_id=CODE, enabled=True
                ),
                ResourcePermission(
                    role_id=role.id, resource_type="element", resource_id=CODE, enabled=True
                ),
            ]
        )
        await db_session.flush()

        granted = await PermissionService(db_session).get_users_with_element_permission(
            [admin.id, direct.id, inherited.id, nobody.id], CODE
        )

        # 三条来源都要算「有权限」：少算一条会让该提示的人没提示
        assert int(admin.id) in granted
        assert int(direct.id) in granted
        assert int(inherited.id) in granted
        # 没人授权的人不能被误标成「可管理」，否则提示形同虚设
        assert int(nobody.id) not in granted
    finally:
        await _cleanup(db_session, mark)


async def test_disabled_permission_rows_are_ignored(db_session):
    mark = uuid.uuid4().hex[:8]
    try:
        disabled = await _make_user(db_session, mark)
        db_session.add(
            ResourcePermission(
                user_id=disabled.id, resource_type="element", resource_id=CODE, enabled=False
            )
        )
        await db_session.flush()

        granted = await PermissionService(db_session).get_users_with_element_permission(
            [disabled.id], CODE
        )

        # 禁用行仍留在库里（用户页展示用），但不能再赋予能力
        assert int(disabled.id) not in granted
    finally:
        await _cleanup(db_session, mark)


async def test_empty_input_skips_queries(db_session):
    service = PermissionService(db_session)

    assert await service.get_users_with_element_permission([], CODE) == set()
    assert await service.get_users_with_element_permission([1, 2], "  ") == set()
