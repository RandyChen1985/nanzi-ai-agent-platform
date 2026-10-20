import pytest
import pytest_asyncio
from app.services.permission_service import PermissionService
from app.models.permission import ResourcePermission
from app.models.user import User
from app.schemas.permission import PermissionUpdate
from sqlalchemy import select, delete

@pytest.fixture
async def test_user(db_session):
    user = User(user_name="test_perm_user", role="user", status=1, api_key_hash="hash_user")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    yield user
    # Cleanup
    await db_session.execute(delete(ResourcePermission).where(ResourcePermission.user_id == user.id))
    await db_session.delete(user)
    await db_session.commit()

@pytest.fixture
async def admin_user(db_session):
    user = User(user_name="test_perm_admin", role="admin", status=1, api_key_hash="hash_admin")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    yield user
    await db_session.delete(user)
    await db_session.commit()

@pytest.mark.asyncio
async def test_update_and_get_permissions(db_session, test_user):
    service = PermissionService(db_session)
    
    # 1. Update Permissions
    update_data = PermissionUpdate(
        agents=["agent-1", "agent-2"],
        datasets=["ds-1"],
        apis=["v1.chat"],
        metadata=["meta-1"]
    )
    
    await service.update_user_permissions(test_user.id, update_data)
    
    # 2. Verify DB
    stmt = select(ResourcePermission).where(ResourcePermission.user_id == test_user.id)
    result = await db_session.execute(stmt)
    perms = result.scalars().all()
    assert len(perms) == 5 # 2 agents + 1 dataset + 1 api + 1 metadata
    
    # 3. Verify Get
    user_perms = await service.get_user_permissions(test_user.id)
    assert set(user_perms.permissions.agents) == {"agent-1", "agent-2"}
    assert user_perms.permissions.datasets == ["ds-1"]
    assert user_perms.permissions.apis == ["v1.chat"]
    assert user_perms.permissions.metadata == ["meta-1"]

@pytest.mark.asyncio
async def test_check_permission_logic(db_session, test_user):
    service = PermissionService(db_session)
    
    # Init perms
    await service.update_user_permissions(test_user.id, PermissionUpdate(
        agents=["allowed-agent"],
        datasets=[],
        apis=[]
    ))
    
    # Check Allowed
    assert await service.check_permission(test_user.id, "agent", "allowed-agent") is True
    
    # Check Denied
    assert await service.check_permission(test_user.id, "agent", "denied-agent") is False
    assert await service.check_permission(test_user.id, "dataset", "any-ds") is False

@pytest.mark.asyncio
async def test_admin_bypass(db_session, admin_user):
    service = PermissionService(db_session)
    
    # Admin has NO explicit permissions
    # But check_permission should return True
    assert await service.check_permission(admin_user.id, "agent", "random-agent") is True
    assert await service.check_permission(admin_user.id, "dataset", "random-ds") is True

@pytest.mark.asyncio
async def test_cache_invalidation(db_session, test_user):
    service = PermissionService(db_session)
    
    # 1. Set initial
    await service.update_user_permissions(test_user.id, PermissionUpdate(agents=["a1"]))
    
    # 2. Access to trigger cache
    p1 = await service.get_user_permissions(test_user.id)
    assert "a1" in p1.permissions.agents
    
    # 3. Update (should invalidate cache)
    await service.update_user_permissions(test_user.id, PermissionUpdate(agents=["a2"]))
    
    # 4. Access again (should get new data from DB, not old cache)
    p2 = await service.get_user_permissions(test_user.id)
    assert "a2" in p2.permissions.agents
    assert "a1" not in p2.permissions.agents


@pytest.mark.asyncio
async def test_menu_id_stored_as_element_is_read_back_as_menu(db_session, test_user):
    """历史脏数据：element 行里存 menu:* 时，读取必须归位到 menus。

    用户反馈「勾了系统配置页面、侧栏却不显示」的根因：旧前端级联把子菜单 id
    写进了 elements，而侧栏 hasMenuPerm() 与后端 require_permission("menu", ...)
    都只查 menus。这里钉住读取层兜底，迁移执行前也能正确显示。
    """
    db_session.add_all([
        ResourcePermission(
            user_id=test_user.id,
            resource_type="element",
            resource_id="menu:system:config",
        ),
        ResourcePermission(
            user_id=test_user.id,
            resource_type="element",
            resource_id="element:system:config_save",
        ),
    ])
    await db_session.commit()

    service = PermissionService(db_session)
    perms = await service.get_user_permissions(test_user.id)

    assert "menu:system:config" in perms.permissions.menus
    assert "menu:system:config" not in perms.permissions.elements
    assert "element:system:config_save" in perms.permissions.elements


@pytest.mark.asyncio
async def test_update_permissions_normalizes_menu_ids_submitted_as_elements(db_session, test_user):
    """旧 bundle 把 menu:* 提交进 elements 时，落库必须写成 menu 类型。

    否则每保存一次就重新制造脏数据；新前端不会这样提交，但线上可能还挂着旧页面。
    同一 id 同时出现在 menus 与 elements 时（旧前端整份提交的并集）只允许落一条。
    """
    service = PermissionService(db_session)
    await service.update_user_permissions(
        test_user.id,
        PermissionUpdate(
            menus=["menu:system:config"],
            elements=["menu:system:config", "element:system:config_save"],
        ),
    )

    rows = (await db_session.execute(
        select(ResourcePermission).where(ResourcePermission.user_id == test_user.id)
    )).scalars().all()
    by_id = {row.resource_id: row.resource_type for row in rows}

    assert by_id["menu:system:config"] == "menu"
    assert by_id["element:system:config_save"] == "element"
    assert [row.resource_id for row in rows].count("menu:system:config") == 1

    perms = await service.get_user_permissions(test_user.id)
    assert "menu:system:config" in perms.permissions.menus
    assert "element:system:config_save" in perms.permissions.elements
