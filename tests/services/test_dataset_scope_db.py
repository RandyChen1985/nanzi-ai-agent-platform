"""数据集三层授权的数据库集成测试。

纯逻辑在 ``test_dataset_scope_permissions.py`` 里覆盖；这里补真实 SQL 路径：
历史行兼容（无扩展列也要能用）、``scope`` JSON 的读写往返、全员基线、过期行忽略。

数据只 flush 不 commit，用例结束统一 rollback —— 不往共享开发库写残留。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from app.models.permission import ResourcePermission
from app.services import dataset_scope_service as dss

TEST_USER_ID = 987_654_321


def _new_dataset_id() -> int:
    return 900_000_000 + random.randint(1, 999_999)


async def _add_grant(
    session,
    dataset_id: int,
    *,
    user_id: int | None = None,
    role_id: int | None = None,
    enabled: bool = True,
) -> ResourcePermission:
    perm = ResourcePermission(
        resource_type="metadata",
        resource_id=str(dataset_id),
        user_id=user_id,
        role_id=role_id,
        enabled=enabled,
    )
    session.add(perm)
    await session.flush()
    return perm


@pytest.mark.asyncio
async def test_legacy_grant_without_scope_means_all_tables(db_session):
    """没有扩展列（或值为 NULL）的历史行必须等价于「数据集内全部表」。"""
    dataset_id = _new_dataset_id()
    await _add_grant(db_session, dataset_id, user_id=TEST_USER_ID)

    try:
        scopes = await dss.resolve_user_dataset_scopes(
            db_session, TEST_USER_ID, dataset_ids=[dataset_id]
        )
        scope = scopes.get(dataset_id)
        assert scope is not None
        assert scope.mode == dss.MODE_ALL
        assert scope.row_policy == dss.ROW_POLICY_INHERIT
        assert scope.sources == (dss.SUBJECT_USER,)
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_disabled_grant_is_ignored(db_session):
    dataset_id = _new_dataset_id()
    await _add_grant(db_session, dataset_id, user_id=TEST_USER_ID, enabled=False)

    try:
        scopes = await dss.resolve_user_dataset_scopes(
            db_session, TEST_USER_ID, dataset_ids=[dataset_id]
        )
        assert scopes == {}
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_table_scope_roundtrip_when_migration_applied(db_session):
    """迁移执行后：写 list 范围 → 解析出同样的表 id；写 custom 行级条件也能读回。"""
    if not await dss.supports_table_scope(db_session):
        pytest.skip("库结构尚未执行 V205/V105 迁移")

    dataset_id = _new_dataset_id()
    perm = await _add_grant(db_session, dataset_id, user_id=TEST_USER_ID)

    try:
        wrote = await dss.update_grant_scope(
            db_session,
            int(perm.id),
            subject_type=dss.SUBJECT_USER,
            scope={"mode": "list", "tables": [11, 12]},
            row_policy=dss.ROW_POLICY_CUSTOM,
            row_condition="fact_a.dept_code = 'FIN'",
            expire_at=None,
        )
        assert wrote is True
        await db_session.flush()

        scope = await dss.resolve_dataset_scope(db_session, TEST_USER_ID, dataset_id)
        assert scope is not None
        assert scope.mode == dss.MODE_LIST
        assert scope.table_ids == frozenset({11, 12})
        assert scope.row_policy == dss.ROW_POLICY_CUSTOM
        assert scope.row_condition == "fact_a.dept_code = 'FIN'"
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_expired_grant_is_ignored_when_migration_applied(db_session):
    if not await dss.supports_table_scope(db_session):
        pytest.skip("库结构尚未执行 V205/V105 迁移")

    dataset_id = _new_dataset_id()
    perm = await _add_grant(db_session, dataset_id, user_id=TEST_USER_ID)

    try:
        await dss.update_grant_scope(
            db_session,
            int(perm.id),
            subject_type=dss.SUBJECT_USER,
            scope={"mode": "all", "tables": []},
            row_policy=dss.ROW_POLICY_INHERIT,
            row_condition=None,
            expire_at=datetime.now() - timedelta(days=1),
        )
        await db_session.flush()

        scopes = await dss.resolve_user_dataset_scopes(
            db_session, TEST_USER_ID, dataset_ids=[dataset_id]
        )
        assert scopes == {}
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_all_members_grants_are_listed_when_migration_applied(db_session):
    if not await dss.supports_table_scope(db_session):
        pytest.skip("库结构尚未执行 V205/V105 迁移")

    dataset_id = _new_dataset_id()
    perm = await _add_grant(db_session, dataset_id)

    try:
        await dss.update_grant_scope(
            db_session,
            int(perm.id),
            subject_type=dss.SUBJECT_ALL,
            scope={"mode": "list", "tables": [3]},
            row_policy=dss.ROW_POLICY_INHERIT,
            row_condition=None,
            expire_at=None,
        )
        await db_session.flush()

        grants = await dss.all_members_grants(db_session)
        assert ("metadata", str(dataset_id)) in grants
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_user_entry_overrides_role_entry_through_sql(db_session):
    """用户级存在时覆盖角色级：角色给全部表，用户只给 2 张 → 最终 2 张。"""
    if not await dss.supports_table_scope(db_session):
        pytest.skip("库结构尚未执行 V205/V105 迁移")

    dataset_id = _new_dataset_id()
    role_id = 880_000 + random.randint(1, 999)
    role_perm = await _add_grant(db_session, dataset_id, role_id=role_id)
    user_perm = await _add_grant(db_session, dataset_id, user_id=TEST_USER_ID)

    from app.models.permission import UserRoleRelation

    relation = UserRoleRelation(user_id=TEST_USER_ID, role_id=role_id)
    db_session.add(relation)
    await db_session.flush()

    try:
        await dss.update_grant_scope(
            db_session,
            int(role_perm.id),
            subject_type=dss.SUBJECT_ROLE,
            scope={"mode": "all", "tables": []},
            row_policy=dss.ROW_POLICY_INHERIT,
            row_condition=None,
            expire_at=None,
        )
        await dss.update_grant_scope(
            db_session,
            int(user_perm.id),
            subject_type=dss.SUBJECT_USER,
            scope={"mode": "list", "tables": [21, 22]},
            row_policy=dss.ROW_POLICY_INHERIT,
            row_condition=None,
            expire_at=None,
        )
        await db_session.flush()

        scope = await dss.resolve_dataset_scope(db_session, TEST_USER_ID, dataset_id)
        assert scope is not None
        assert scope.mode == dss.MODE_LIST
        assert scope.table_ids == frozenset({21, 22})
        assert scope.sources == (dss.SUBJECT_USER,)
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_rebuild_keeps_table_scope_for_user(db_session):
    """用户管理里整份重建数据集勾选时，表范围/行级策略必须被回填，不能退化成全部表。"""
    if not await dss.supports_table_scope(db_session):
        pytest.skip("库结构尚未执行 V205/V105 迁移")

    from sqlalchemy import delete

    from app.services.permission_service import PermissionService

    dataset_id = _new_dataset_id()
    perm = await _add_grant(db_session, dataset_id, user_id=TEST_USER_ID)
    await dss.update_grant_scope(
        db_session,
        int(perm.id),
        subject_type=dss.SUBJECT_USER,
        scope={"mode": "list", "tables": [31, 32]},
        row_policy=dss.ROW_POLICY_CUSTOM,
        row_condition="fact_a.dept_code = 'FIN'",
        expire_at=None,
    )
    await db_session.flush()

    service = PermissionService(db_session)
    snapshot = await service._snapshot_metadata_scopes("user_id", TEST_USER_ID)
    assert str(dataset_id) in snapshot

    # 模拟「整份重建」：删掉旧行，只按 id 重新插入一条干净的行
    await db_session.execute(
        delete(ResourcePermission).where(
            ResourcePermission.user_id == TEST_USER_ID,
            ResourcePermission.resource_type == "metadata",
        )
    )
    await db_session.flush()
    db_session.add(
        ResourcePermission(
            resource_type="metadata", resource_id=str(dataset_id), user_id=TEST_USER_ID
        )
    )
    await db_session.flush()

    # 重建后若不回填，scope 为 NULL = 全部表（权限放大）
    naive = await dss.resolve_dataset_scope(db_session, TEST_USER_ID, dataset_id)
    assert naive is not None and naive.mode == dss.MODE_ALL

    try:
        await service._restore_metadata_scopes("user_id", TEST_USER_ID, snapshot)
        await db_session.flush()

        restored = await dss.resolve_dataset_scope(db_session, TEST_USER_ID, dataset_id)
        assert restored is not None
        assert restored.mode == dss.MODE_LIST
        assert restored.table_ids == frozenset({31, 32})
        assert restored.row_policy == dss.ROW_POLICY_CUSTOM
        assert restored.row_condition == "fact_a.dept_code = 'FIN'"
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_rebuild_keeps_table_scope_for_role(db_session):
    """角色侧同一条链路同样要保住范围。"""
    if not await dss.supports_table_scope(db_session):
        pytest.skip("库结构尚未执行 V205/V105 迁移")

    from sqlalchemy import delete

    from app.services.permission_service import PermissionService

    dataset_id = _new_dataset_id()
    role_id = 870_000 + random.randint(1, 999)
    perm = await _add_grant(db_session, dataset_id, role_id=role_id)

    from app.models.permission import UserRoleRelation

    db_session.add(UserRoleRelation(user_id=TEST_USER_ID, role_id=role_id))
    await db_session.flush()
    await dss.update_grant_scope(
        db_session,
        int(perm.id),
        subject_type=dss.SUBJECT_ROLE,
        scope={"mode": "list", "tables": [41]},
        row_policy=dss.ROW_POLICY_NONE,
        row_condition=None,
        expire_at=None,
    )
    await db_session.flush()

    service = PermissionService(db_session)
    snapshot = await service._snapshot_metadata_scopes("role_id", role_id)
    assert str(dataset_id) in snapshot

    await db_session.execute(
        delete(ResourcePermission).where(
            ResourcePermission.role_id == role_id,
            ResourcePermission.resource_type == "metadata",
        )
    )
    await db_session.flush()
    db_session.add(
        ResourcePermission(
            resource_type="metadata", resource_id=str(dataset_id), role_id=role_id
        )
    )
    await db_session.flush()

    try:
        await service._restore_metadata_scopes("role_id", role_id, snapshot)
        await db_session.flush()

        scope = await dss.resolve_dataset_scope(db_session, TEST_USER_ID, dataset_id)
        assert scope is not None
        assert scope.mode == dss.MODE_LIST
        assert scope.table_ids == frozenset({41})
        assert scope.row_policy == dss.ROW_POLICY_NONE
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_pure_dataset_grant_is_not_snapshotted(db_session):
    """没有表级/行级配置的行不该被快照，避免把 NULL 改写成显式配置。"""
    if not await dss.supports_table_scope(db_session):
        pytest.skip("库结构尚未执行 V205/V105 迁移")

    from app.services.permission_service import PermissionService

    dataset_id = _new_dataset_id()
    await _add_grant(db_session, dataset_id, user_id=TEST_USER_ID)

    try:
        snapshot = await PermissionService(db_session)._snapshot_metadata_scopes(
            "user_id", TEST_USER_ID
        )
        assert snapshot == {}
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_rebuild_keeps_column_scope_for_user(db_session):
    """整份重建时字段级（列）收窄也要回填。

    ``columns`` 和 ``tables`` 同在 ``scope`` JSON 里，回填是整个 scope 覆盖写回；
    快照只留 ``{mode, tables}`` 的话，管理员在用户管理页勾一次数据集，就把别人配好的
    「只给这几列 / 排除 id_card」静默放大成全列可见——与表级收窄当初丢配置同一类问题。
    """
    if not await dss.supports_table_scope(db_session):
        pytest.skip("库结构尚未执行 V205/V105 迁移")

    from sqlalchemy import delete

    from app.services.permission_service import PermissionService

    dataset_id = _new_dataset_id()
    table_id = 500_000 + random.randint(1, 9999)
    perm = await _add_grant(db_session, dataset_id, user_id=TEST_USER_ID)
    await dss.update_grant_scope(
        db_session,
        int(perm.id),
        subject_type=dss.SUBJECT_USER,
        scope={
            "mode": "all",
            "tables": [],
            "columns": {str(table_id): {"mode": "exclude", "names": ["ID_Card"]}},
        },
        row_policy=dss.ROW_POLICY_NONE,
        row_condition=None,
        expire_at=None,
    )
    await db_session.flush()

    service = PermissionService(db_session)
    snapshot = await service._snapshot_metadata_scopes("user_id", TEST_USER_ID)
    saved = snapshot[str(dataset_id)]["scope"]
    assert saved.get("columns"), "快照必须带上字段级规则，否则回填时会被整份覆盖成空"

    # 模拟用户管理页的「整份重建」
    await db_session.execute(
        delete(ResourcePermission).where(
            ResourcePermission.user_id == TEST_USER_ID,
            ResourcePermission.resource_type == "metadata",
        )
    )
    await db_session.flush()
    db_session.add(
        ResourcePermission(
            resource_type="metadata", resource_id=str(dataset_id), user_id=TEST_USER_ID
        )
    )
    await db_session.flush()

    try:
        await service._restore_metadata_scopes("user_id", TEST_USER_ID, snapshot)
        await db_session.flush()

        after = await service._snapshot_metadata_scopes("user_id", TEST_USER_ID)
        restored = after[str(dataset_id)]["scope"]["columns"][str(table_id)]
        assert restored["mode"] == "exclude"
        assert [name.lower() for name in restored["names"]] == ["id_card"]
    finally:
        await db_session.rollback()
