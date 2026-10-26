"""通知识库访问判定的集成行为：真实库 + 真实部门树。

纯函数层已有穷举测试（``test_knowledge_access_visibility.py``），这里补的是
``get_knowledge_base_access`` 的 SQL 部分——部门 path 前缀匹配、成员关系 join、
系统默认库读取，这些写错时纯函数测试照样全绿。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.models.department import Department, UserDepartment
from app.models.knowledge import KnowledgeBaseMetadata
from app.models.permission import ResourcePermission
from app.models.user import User
from app.services.permission_service import PermissionService


class _Fixture:
    """一次构建的部门树 + 用户 + 知识库，测试结束后按前缀清理。"""

    def __init__(self, mark: str):
        self.mark = mark



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


async def _build(db_session):
    mark = uuid.uuid4().hex[:8]
    fixture = _Fixture(mark)

    # 部门树：root(/) → 技术中心(/x/) → 后端组(/x/y/)
    root = Department(code=f"kb-root-{mark}", name="根部门", path="/", parent_id=None)
    db_session.add(root)
    await db_session.flush()
    root.path = f"/{root.id}/"
    tech = Department(
        code=f"kb-tech-{mark}", name="技术中心", path="/", parent_id=root.id
    )
    db_session.add(tech)
    await db_session.flush()
    tech.path = f"{root.path}{tech.id}/"
    backend = Department(
        code=f"kb-backend-{mark}", name="后端组", path="/", parent_id=tech.id
    )
    sibling = Department(
        code=f"kb-sibling-{mark}", name="兄弟部门", path="/", parent_id=root.id
    )
    db_session.add_all([backend, sibling])
    await db_session.flush()
    backend.path = f"{tech.path}{backend.id}/"
    sibling.path = f"{root.path}{sibling.id}/"

    def make_user(tag: str) -> User:
        return User(
            user_name=f"kb-{tag}-{mark}",
            role="user",
            status=1,
            api_key_hash=f"hash_{uuid.uuid4().hex}",
        )

    creator = make_user("creator")
    teammate = make_user("teammate")  # 技术中心成员：应能读 team 库
    outsider = make_user("outsider")  # 兄弟部门成员：不应看到 team 库
    db_session.add_all([creator, teammate, outsider])
    await db_session.flush()

    db_session.add_all(
        [
            UserDepartment(user_id=teammate.id, dept_id=tech.id, is_primary=1),
            UserDepartment(user_id=outsider.id, dept_id=sibling.id, is_primary=1),
        ]
    )

    team_kb = KnowledgeBaseMetadata(
        ragflow_dataset_id=f"ds-team-{mark}",
        name="部门级库",
        visibility="team",
        owner_dept_id=tech.id,
        created_by=creator.user_name,
        status="active",
    )
    private_kb = KnowledgeBaseMetadata(
        ragflow_dataset_id=f"ds-private-{mark}",
        name="私有库",
        visibility="private",
        created_by=creator.user_name,
        status="active",
    )
    public_kb = KnowledgeBaseMetadata(
        ragflow_dataset_id=f"ds-public-{mark}",
        name="公共级库",
        visibility="public",
        created_by=creator.user_name,
        status="active",
    )
    db_session.add_all([team_kb, private_kb, public_kb])
    await db_session.flush()

    return fixture, {
        "creator": creator,
        "teammate": teammate,
        "outsider": outsider,
        "team": team_kb.ragflow_dataset_id,
        "private": private_kb.ragflow_dataset_id,
        "public": public_kb.ragflow_dataset_id,
        "tech": tech,
    }


async def _cleanup(db_session, mark: str):
    await db_session.execute(
        delete(ResourcePermission).where(
            ResourcePermission.resource_type == "dataset",
            ResourcePermission.resource_id.like(f"ds-%-{mark}"),
        )
    )
    await db_session.execute(
        delete(KnowledgeBaseMetadata).where(
            KnowledgeBaseMetadata.ragflow_dataset_id.like(f"ds-%-{mark}")
        )
    )
    await db_session.execute(
        delete(UserDepartment).where(
            UserDepartment.dept_id.in_(
                select(Department.id).where(Department.code.like(f"kb-%-{mark}"))
            )
        )
    )
    await db_session.execute(
        delete(Department).where(Department.code.like(f"kb-%-{mark}"))
    )
    await db_session.execute(delete(User).where(User.user_name.like(f"kb-%-{mark}")))
    await db_session.flush()



@pytest.mark.asyncio
async def test_team_scope_grants_subtree_members_read_only(db_session):
    fixture, data = await _build(db_session)
    service = PermissionService(db_session)
    try:
        teammate = await service.get_knowledge_base_access(
            data["teammate"].id, data["teammate"].user_name
        )
        # 技术中心成员能读团队库；兄弟部门看不到
        assert data["team"] in teammate["accessible_ids"]
        assert data["private"] not in teammate["accessible_ids"]
        # 部门成员只读：写权限只给创建人
        assert data["team"] not in teammate["writable_ids"]

        outsider = await service.get_knowledge_base_access(
            data["outsider"].id, data["outsider"].user_name
        )
        assert data["team"] not in outsider["accessible_ids"]

        creator = await service.get_knowledge_base_access(
            data["creator"].id, data["creator"].user_name
        )
        # 创建人自己的三个库都可读可写（含 private）
        assert {data["team"], data["private"], data["public"]} <= creator["accessible_ids"]
        assert {data["team"], data["private"], data["public"]} <= creator["writable_ids"]
    finally:
        await _cleanup(db_session, fixture.mark)


@pytest.mark.asyncio
async def test_sub_department_is_included_in_owner_subtree(db_session):
    """归属「技术中心」的库，其下级「后端组」成员也要能读。"""
    fixture, data = await _build(db_session)
    service = PermissionService(db_session)
    try:
        backend_member = User(
            user_name=f"kb-sub-{fixture.mark}",
            role="user",
            status=1,
            api_key_hash=f"hash_{uuid.uuid4().hex}",
        )
        db_session.add(backend_member)
        await db_session.flush()
        backend_dept = (
            await db_session.execute(
                select(Department).where(
                    Department.code.like(f"kb-backend-{fixture.mark}")
                )
            )
        ).scalar_one()
        db_session.add(
            UserDepartment(
                user_id=backend_member.id, dept_id=backend_dept.id, is_primary=1
            )
        )
        await db_session.flush()

        access = await service.get_knowledge_base_access(
            backend_member.id, backend_member.user_name
        )
        assert data["team"] in access["accessible_ids"]
        assert data["team"] not in access["writable_ids"]
    finally:
        await _cleanup(db_session, fixture.mark)


@pytest.mark.asyncio
async def test_private_scope_ignores_existing_grant(db_session):
    fixture, data = await _build(db_session)
    service = PermissionService(db_session)
    try:
        # 给「外人」授予私有库（脏数据）：判定必须无视它
        db_session.add(
            ResourcePermission(
                user_id=data["outsider"].id,
                resource_type="dataset",
                resource_id=data["private"],
                enabled=True,
            )
        )
        await db_session.flush()

        outsider = await service.get_knowledge_base_access(
            data["outsider"].id, data["outsider"].user_name
        )
        assert data["private"] not in outsider["accessible_ids"]
    finally:
        await _cleanup(db_session, fixture.mark)


@pytest.mark.asyncio
async def test_private_datasets_are_not_shared_through_agent_binding(db_session):
    """智能体绑定会跳过使用者权限校验，但个人私有不能跟着被代理放行。

    否则任何"被授权过某个库"的人都能建个智能体绑上它，把内容二次分发给全体使用者。
    """
    fixture, data = await _build(db_session)
    service = PermissionService(db_session)
    try:
        creator_name = data["creator"].user_name
        ids = [data["private"], data["public"], data["team"]]

        # 创建人本人：自己的私有库不摘
        assert await service.find_private_datasets_not_owned(ids, creator_name) == set()

        # 其他人：只摘私有，公有/部门库照常按权限判定
        denied = await service.find_private_datasets_not_owned(
            ids, data["teammate"].user_name
        )
        assert denied == {data["private"]}

        # 空集合 / 元数据里查不到的库都不摘（沿用「未同步库按授权放行」的老口径）
        assert await service.find_private_datasets_not_owned([], creator_name) == set()
        assert (
            await service.find_private_datasets_not_owned(
                ["ds-not-in-metadata"], creator_name
            )
            == set()
        )
    finally:
        await _cleanup(db_session, fixture.mark)


@pytest.mark.asyncio
async def test_admin_can_see_and_write_other_users_private_dataset(db_session):
    """产品决策：管理员对他人私有库有完全的读/写能力。

    这不是"private 失效"的 bug —— 管理员口径是全局不受限（``None``），
    用于排障、清理、审计。若哪天要收紧成"管理员也看不到私有库内容"，
    必须同时改本测试和 ``get_knowledge_base_access`` 的 docstring。
    """
    fixture, data = await _build(db_session)
    service = PermissionService(db_session)
    try:
        # 管理员判定来自 ai_agent_users.role 字段（PermissionService.is_admin_user），
        # 与业务角色表无关，所以这里只需要一个 role="admin" 的用户
        admin = User(
            user_name=f"kb-admin-{fixture.mark}",
            role="admin",
            status=1,
            api_key_hash=f"hash_{uuid.uuid4().hex}",
        )
        db_session.add(admin)
        await db_session.flush()

        access = await service.get_knowledge_base_access(admin.id, admin.user_name)
        assert access["is_admin"] is True
        # None = 不受限，不是"空权限"：消费方据此放行全部库（含他人私有库）
        assert access["accessible_ids"] is None
        assert access["writable_ids"] is None
        assert service.is_admin_user(["admin"]) is True
        assert service.is_admin_user(["default"]) is False
    finally:
        await _cleanup(db_session, fixture.mark)


@pytest.mark.asyncio
async def test_transfer_owner_moves_management_and_revokes_previous(db_session):
    """转移归属是「换人」不是「加人」：旧归属人必须立刻失去访问。

    集成层要验的是 SQL 列真的查出来并参与判定（纯函数测试喂的是内存元组）。
    """
    from app.services.knowledge_base_service import KnowledgeBaseMetadataService

    fixture, data = await _build(db_session)
    service = PermissionService(db_session)
    metadata_service = KnowledgeBaseMetadataService(db_session)
    try:
        before = await service.get_knowledge_base_access(
            data["creator"].id, data["creator"].user_name
        )
        assert data["private"] in before["writable_ids"]

        await metadata_service.transfer_owner(
            data["private"],
            target_user_id=data["teammate"].id,
            user_name=data["creator"].user_name,
        )
        await db_session.flush()

        previous_owner = await service.get_knowledge_base_access(
            data["creator"].id, data["creator"].user_name
        )
        assert data["private"] not in previous_owner["accessible_ids"]
        assert data["private"] not in previous_owner["writable_ids"]

        new_owner = await service.get_knowledge_base_access(
            data["teammate"].id, data["teammate"].user_name
        )
        assert data["private"] in new_owner["accessible_ids"]
        assert data["private"] in new_owner["writable_ids"]
    finally:
        await _cleanup(db_session, fixture.mark)
