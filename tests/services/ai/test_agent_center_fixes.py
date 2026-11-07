"""本轮智能体中心修复的后端回归用例。"""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete, select

from app.models.agent import AIAgent, AIAgentVersion
from app.models.agent_system import AgentSystemRequest
from app.models.portal_notification import PortalNotification
from app.services.ai.agent_manager import AgentManagerService, AgentNotReadyError
from app.services.ai.agent_system_columns import (
    SYSTEM_STATUS_NONE,
    read_system_status,
    reset_probe_state_for_tests,
    write_system_status,
)
from app.services.ai.agent_system_publication_service import (
    REQUEST_TYPE_RELEASE,
    STATUS_APPROVED,
    AgentSystemPublicationService,
)


@pytest.mark.asyncio
async def test_request_detail_exposes_request_type_and_version_number(db_session):
    """审核详情必须带上申请类型与目标版本号。

    列表接口一直有这两个字段，详情漏掉后前端 ``isVersionRelease`` 恒为 false，
    VERSION_RELEASE 会被当成 SYSTEM_PUBLISH 展示（按钮文案和提示全错）。
    """
    reset_probe_state_for_tests()
    mark = uuid.uuid4().hex[:8]
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"detail-agent-{mark}",
        display_name="详情字段测试",
        created_by=f"owner-{mark}",
        is_system=True,
        is_enabled=True,
        engine_type="LOCAL",
    )
    version = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=7,
        status="DRAFT",
        system_prompt="待上线内容",
    )
    request = AgentSystemRequest(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        request_type=REQUEST_TYPE_RELEASE,
        snapshot_version_id=version.id,
        status="PENDING",
        requested_at=datetime.now(),
    )
    db_session.add_all([agent, version, request])
    await db_session.commit()

    try:
        detail = await AgentSystemPublicationService.request_detail(
            db_session, request.id
        )
        assert detail["request_type"] == REQUEST_TYPE_RELEASE
        assert detail["version_number"] == 7
    finally:
        await db_session.execute(
            delete(AgentSystemRequest).where(AgentSystemRequest.agent_id == agent.id)
        )
        await db_session.execute(
            delete(AIAgentVersion).where(AIAgentVersion.agent_id == agent.id)
        )
        await db_session.execute(delete(AIAgent).where(AIAgent.id == agent.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_release_request_blocks_unready_draft(db_session):
    """CHATBI 草稿缺少查数工具时，提交上线申请应直接被拦下。

    否则申请单能建、待审能冻结，管理员点通过时才在发布路径报未就绪，
    申请单和冻结状态都留在原地。
    """
    reset_probe_state_for_tests()
    mark = uuid.uuid4().hex[:8]
    owner_name = f"owner-{mark}"
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"ready-agent-{mark}",
        display_name="未就绪申请测试",
        created_by=owner_name,
        is_system=True,
        is_enabled=True,
        engine_type="LOCAL",
        agent_type="CHATBI",
        capabilities=["data_query"],
    )
    published = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=1,
        status="PUBLISHED",
        system_prompt="线上内容",
        tools=[],
    )
    draft = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=2,
        status="DRAFT",
        system_prompt="缺查数工具",
        tools=[],
    )
    db_session.add_all([agent, published, draft])
    await db_session.commit()

    try:
        with pytest.raises(AgentNotReadyError) as exc:
            await AgentSystemPublicationService.submit_release_request(
                db_session,
                agent.id,
                draft.id,
                {"role": "user", "user_name": owner_name, "user_id": 1},
            )
        assert "data_query_tool" in exc.value.missing
    finally:
        await db_session.execute(
            delete(AgentSystemRequest).where(AgentSystemRequest.agent_id == agent.id)
        )
        await db_session.execute(
            delete(PortalNotification).where(
                PortalNotification.resource_type == "agent",
                PortalNotification.resource_id == str(agent.id),
            )
        )
        await db_session.execute(
            delete(AIAgentVersion).where(AIAgentVersion.agent_id == agent.id)
        )
        await db_session.execute(delete(AIAgent).where(AIAgent.id == agent.id))
        await db_session.commit()


@pytest.mark.no_infrastructure
@pytest.mark.asyncio
async def test_invalidate_access_caches_clears_permission_and_router(monkeypatch):
    """授权变化后必须同时清权限缓存和路由缓存。"""
    from app.services.ai.router_service import router_service
    from app.services.permission_service import PermissionService

    calls = {}

    async def fake_invalidate(self, user_ids):
        calls["user_ids"] = list(user_ids)

    monkeypatch.setattr(
        PermissionService, "invalidate_cached_permissions_for_users", fake_invalidate
    )
    monkeypatch.setattr(
        router_service, "invalidate_cache", lambda: calls.setdefault("router", True)
    )

    await AgentSystemPublicationService._invalidate_access_caches(
        object(), [7, None, 8]
    )

    assert calls["user_ids"] == [7, 8]
    assert calls["router"] is True


@pytest.mark.asyncio
async def test_admin_direct_publish_closes_pending_release_request(db_session):
    """管理员绕过审核直接发布待审版本时，申请单与冻结状态要一起收尾。"""
    reset_probe_state_for_tests()
    mark = uuid.uuid4().hex[:8]
    owner_name = f"owner-{mark}"
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"direct-publish-{mark}",
        display_name="直接发布收尾测试",
        created_by=owner_name,
        is_system=True,
        is_enabled=True,
        engine_type="LOCAL",
        agent_type="GENERAL",
        capabilities=["general_chat"],
    )
    published = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=1,
        status="PUBLISHED",
        system_prompt="线上内容",
        tools=[],
    )
    draft = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=2,
        status="DRAFT",
        system_prompt="待审内容",
        tools=[],
    )
    request = AgentSystemRequest(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        request_type=REQUEST_TYPE_RELEASE,
        snapshot_version_id=draft.id,
        status="PENDING",
        requested_by=1,
        requested_at=datetime.now(),
    )
    db_session.add_all([agent, published, draft, request])
    await db_session.commit()
    await write_system_status(
        db_session,
        agent.id,
        status="PENDING_REVIEW",
        pending_request_id=request.id,
    )
    await db_session.commit()

    try:
        published_ok = await AgentManagerService.publish_version(
            db_session,
            agent.id,
            draft.id,
            user={"role": "admin", "user_name": "admin", "user_id": 1},
        )
        assert published_ok is True

        await db_session.refresh(request)
        assert request.status == STATUS_APPROVED
        status, pending_id = await read_system_status(db_session, agent.id)
        assert status == SYSTEM_STATUS_NONE
        assert pending_id is None
    finally:
        from app.models.agent_system import AgentSystemEvent
        from app.models.portal_notification import PortalNotification

        await db_session.execute(
            delete(PortalNotification).where(
                PortalNotification.resource_type == "agent",
                PortalNotification.resource_id == str(agent.id),
            )
        )
        await db_session.execute(
            delete(AgentSystemEvent).where(AgentSystemEvent.agent_id == str(agent.id))
        )
        await db_session.execute(
            delete(AgentSystemRequest).where(AgentSystemRequest.agent_id == str(agent.id))
        )
        await db_session.execute(
            delete(AIAgentVersion).where(AIAgentVersion.agent_id == str(agent.id))
        )
        await db_session.execute(delete(AIAgent).where(AIAgent.id == agent.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_delete_agent_cleans_auxiliary_rows_and_avatar(db_session, tmp_path, monkeypatch):
    """删除普通智能体时，申请单/事件/授权/待处理分享/头像都要一起清理。"""
    from app.core import avatar_assets
    from app.models.agent_share import AgentShareRequest
    from app.models.agent_system import AgentSystemEvent
    from app.models.permission import ResourcePermission

    reset_probe_state_for_tests()
    mark = uuid.uuid4().hex[:8]
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"delete-agent-{mark}",
        display_name="删除清理测试",
        created_by=f"owner-{mark}",
        is_system=False,
        is_enabled=True,
        engine_type="LOCAL",
        agent_type="GENERAL",
        capabilities=["general_chat"],
    )
    version = AIAgentVersion(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        version_number=1,
        status="PUBLISHED",
        system_prompt="内容",
        tools=[],
    )
    share = AgentShareRequest(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        recipient_user_id=1,
        status="PENDING",
        snapshot="{}",
    )
    event = AgentSystemEvent(
        id=str(uuid.uuid4()),
        agent_id=agent.id,
        action="PUBLISH",
        created_at=datetime.now(),
    )
    permission = ResourcePermission(
        user_id=1,
        resource_type="agent",
        resource_id=agent.id,
        enabled=True,
    )
    db_session.add_all([agent, version, share, event, permission])
    await db_session.commit()

    monkeypatch.setattr(avatar_assets, "AGENT_AVATAR_DIR", str(tmp_path))
    avatar_file = tmp_path / f"{agent.id}_1.png"
    avatar_file.write_bytes(b"x")

    try:
        deleted = await AgentManagerService.delete_agent(
            db_session,
            agent.id,
            user={"role": "admin", "user_name": "admin", "user_id": 1},
        )
        assert deleted is True
        assert await db_session.get(AIAgent, agent.id) is None
        assert await db_session.get(AgentShareRequest, share.id) is None
        assert await db_session.get(AgentSystemEvent, event.id) is None
        rows = await db_session.execute(
            select(ResourcePermission).where(
                ResourcePermission.resource_type == "agent",
                ResourcePermission.resource_id == str(agent.id),
            )
        )
        assert rows.scalars().all() == []
        assert not avatar_file.exists()
    finally:
        await db_session.execute(
            delete(AgentShareRequest).where(AgentShareRequest.agent_id == str(agent.id))
        )
        await db_session.execute(
            delete(AgentSystemEvent).where(AgentSystemEvent.agent_id == str(agent.id))
        )
        await db_session.execute(
            delete(ResourcePermission).where(
                ResourcePermission.resource_type == "agent",
                ResourcePermission.resource_id == str(agent.id),
            )
        )
        await db_session.execute(
            delete(AgentSystemRequest).where(AgentSystemRequest.agent_id == str(agent.id))
        )
        await db_session.execute(
            delete(AIAgentVersion).where(AIAgentVersion.agent_id == str(agent.id))
        )
        await db_session.execute(delete(AIAgent).where(AIAgent.id == agent.id))
        await db_session.commit()
