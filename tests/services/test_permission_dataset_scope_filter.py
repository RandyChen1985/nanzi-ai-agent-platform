"""个人私有知识库不接受授权：写入侧必须剔除。

口径：private 库在判定侧会被直接跳过（见 resolve_knowledge_dataset_access），
写进 ResourcePermission 也永远不会生效。前端已禁用这类项的勾选，后端再兜一层，
避免旧前端或脚本直接调接口留下一堆永不生效的授权记录。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.models.knowledge import KnowledgeBaseMetadata
from app.models.permission import ResourcePermission, Role
from app.schemas.permission import PermissionUpdate
from app.services.permission_service import PermissionService



@pytest.fixture(autouse=True)
async def _require_owner_user_id_column(db_session):
    """V189 迁移未执行时跳过：缺列会直接报 1054，与判定逻辑无关。

    迁移执行后本 fixture 自动放行（不是永久跳过），避免为了跑测试去手动改库。
    """
    from sqlalchemy import text

    try:
        await db_session.execute(
            text("SELECT owner_user_id FROM knowledge_base_metadata LIMIT 0")
        )
    except Exception:
        pytest.skip(
            "需要先执行 db-prod/V189-add_owner_user_id_to_knowledge_base_metadata.sql"
        )
    yield


async def _make_user(db_session):
    from app.models.user import User

    user = User(
        user_name=f"scope_{uuid.uuid4().hex[:8]}",
        role="user",
        status=1,
        api_key_hash=f"hash_{uuid.uuid4().hex}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_role(db_session) -> Role:
    role = Role(code=f"scope_{uuid.uuid4().hex[:8]}", name="私有库过滤测试角色")
    db_session.add(role)
    await db_session.flush()
    return role


async def _make_dataset(db_session, visibility: str) -> str:
    dataset_id = f"ds-scope-{uuid.uuid4().hex[:10]}"
    db_session.add(
        KnowledgeBaseMetadata(
            ragflow_dataset_id=dataset_id,
            name=f"scope-{visibility}",
            visibility=visibility,
            status="active",
        )
    )
    await db_session.flush()
    return dataset_id


async def _dataset_ids_for(db_session, **scope) -> list[str]:
    stmt = select(ResourcePermission.resource_id).where(
        ResourcePermission.resource_type == "dataset",
        *[getattr(ResourcePermission, key) == value for key, value in scope.items()],
    )
    return sorted((await db_session.execute(stmt)).scalars().all())


@pytest.mark.asyncio
async def test_user_permissions_drop_private_dataset(db_session):
    user = await _make_user(db_session)
    public_id = await _make_dataset(db_session, "public")
    team_id = await _make_dataset(db_session, "team")
    private_id = await _make_dataset(db_session, "private")

    service = PermissionService(db_session)
    await service.update_user_permissions(
        user.id,
        PermissionUpdate(datasets=[public_id, team_id, private_id]),
    )

    # 公共级与 TEAM 级照常写入，个人私有被剔除
    assert await _dataset_ids_for(db_session, user_id=user.id) == sorted(
        [public_id, team_id]
    )

    # 收尾：其余用例不依赖这些行
    await db_session.execute(
        delete(ResourcePermission).where(ResourcePermission.user_id == user.id)
    )


@pytest.mark.asyncio
async def test_role_permissions_drop_private_dataset(db_session):
    role = await _make_role(db_session)
    public_id = await _make_dataset(db_session, "public")
    private_id = await _make_dataset(db_session, "private")

    service = PermissionService(db_session)
    await service.update_role_permissions(
        role.id,
        PermissionUpdate(datasets=[public_id, private_id]),
    )

    assert await _dataset_ids_for(db_session, role_id=role.id) == [public_id]

    await db_session.execute(
        delete(ResourcePermission).where(ResourcePermission.role_id == role.id)
    )


@pytest.mark.asyncio
async def test_unknown_dataset_is_still_written(db_session):
    """元数据缺失的库保持旧行为：不能因为「查不到 visibility」就静默丢授权。"""
    user = await _make_user(db_session)
    unsynced_id = f"ds-unsynced-{uuid.uuid4().hex[:10]}"

    service = PermissionService(db_session)
    await service.update_user_permissions(
        user.id,
        PermissionUpdate(datasets=[unsynced_id]),
    )

    assert await _dataset_ids_for(db_session, user_id=user.id) == [unsynced_id]

    await db_session.execute(
        delete(ResourcePermission).where(ResourcePermission.user_id == user.id)
    )
