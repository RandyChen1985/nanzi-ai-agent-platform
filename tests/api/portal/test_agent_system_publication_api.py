"""系统级发布接口：路由顺序、权限、以及发布/下架/审核的端到端串通。

路由顺序是最容易出错的地方：``/system/requests/count`` 必须注册在
``/system/requests/{request_id}`` 之前，否则 ``count`` 会被当成一个申请单 ID，
角标接口直接 404。
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.main import app
from app.models.agent import AIAgent, AIAgentVersion
from app.models.agent_system import AgentSystemEvent, AgentSystemRequest
from app.models.permission import ResourcePermission
from app.models.portal_notification import PortalNotification
from app.services.auth_service import AuthService


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_agent(session, *, owner: str) -> AIAgent:
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"api-sys-{_uid()}",
        display_name="接口测试智能体",
        created_by=owner,
        is_system=False,
        is_enabled=True,
        engine_type="LOCAL",
    )
    session.add(agent)
    await session.flush()
    session.add(
        AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            version_number=1,
            system_prompt="你是接口测试助手",
            model_name="deepseek-chat",
            status="PUBLISHED",
        )
    )
    await session.flush()
    await session.commit()
    return agent


async def _cleanup(*agent_ids: str) -> None:
    from app.core.orm import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AgentSystemRequest).where(AgentSystemRequest.agent_id.in_(list(agent_ids)))
        )
        await session.execute(
            delete(AgentSystemEvent).where(AgentSystemEvent.agent_id.in_(list(agent_ids)))
        )
        await session.execute(
            delete(ResourcePermission).where(
                ResourcePermission.resource_type == "agent",
                ResourcePermission.resource_id.in_(list(agent_ids)),
            )
        )
        await session.execute(
            delete(PortalNotification).where(
                PortalNotification.resource_type == "agent",
                PortalNotification.resource_id.in_(list(agent_ids)),
            )
        )
        await session.execute(delete(AIAgentVersion).where(AIAgentVersion.agent_id.in_(list(agent_ids))))
        await session.execute(delete(AIAgent).where(AIAgent.id.in_(list(agent_ids))))
        await session.commit()


@pytest.mark.asyncio
async def test_request_count_route_not_shadowed(db_session):
    """``/system/requests/count`` 不能被 ``/system/requests/{request_id}`` 抢走。"""
    admin_key = await AuthService.generate_api_key(f"sys_api_{_uid()}", role="admin", db=db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/agents/system/requests/count", headers={"X-API-Key": admin_key}
        )
    assert resp.status_code == 200
    assert isinstance(resp.json()["count"], int)


@pytest.mark.asyncio
async def test_review_endpoints_require_permission(db_session):
    """没有审核权限的普通用户拿不到待审列表。"""
    user_key = await AuthService.generate_api_key(f"sys_user_{_uid()}", role="user", db=db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/agents/system/requests", headers={"X-API-Key": user_key}
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_submit_approve_publish_then_unpublish(db_session):
    """申请 → 通过 → 下架 的完整链路，事件与授权都要落库。"""
    admin_key = await AuthService.generate_api_key(f"sys_api_{_uid()}", role="admin", db=db_session)
    agent = await _make_agent(db_session, owner="api_owner")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-API-Key": admin_key}

            submitted = await client.post(
                f"/api/portal/agents/{agent.id}/system/requests",
                json={"comment": "稳定运行两个月"},
                headers=headers,
            )
            assert submitted.status_code == 200, submitted.text
            request_id = submitted.json()["request_id"]
            assert submitted.json()["status"] == "PENDING"

            listed = await client.get("/api/portal/agents/system/requests", headers=headers)
            assert listed.status_code == 200
            assert any(item["id"] == request_id for item in listed.json())

            detail = await client.get(
                f"/api/portal/agents/system/requests/{request_id}", headers=headers
            )
            assert detail.status_code == 200
            assert detail.json()["snapshot"]["version"]["system_prompt"] == "你是接口测试助手"

            approved = await client.post(
                f"/api/portal/agents/system/requests/{request_id}/approve",
                json={"comment": "已核对"},
                headers=headers,
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["is_system"] is True

            preview = await client.get(
                f"/api/portal/agents/{agent.id}/system/unpublish-preview", headers=headers
            )
            assert preview.status_code == 200
            assert preview.json()["affected_user_count"] >= 0

            unpublished = await client.post(
                f"/api/portal/agents/{agent.id}/system/unpublish",
                json={"reason": "业务迁移", "notify": False},
                headers=headers,
            )
            assert unpublished.status_code == 200, unpublished.text
            assert unpublished.json()["notified_user_count"] == 0

            history = await client.get(
                f"/api/portal/agents/{agent.id}/system/history", headers=headers
            )
            assert history.status_code == 200
            actions = [item.get("action") for item in history.json()["items"]]
            assert "PUBLISH" in actions
            assert "UNPUBLISH" in actions

        from app.core.orm import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            stored = await session.get(AIAgent, agent.id)
            assert stored.is_system is False

            grants = (
                await session.execute(
                    select(ResourcePermission).where(
                        ResourcePermission.resource_type == "agent",
                        ResourcePermission.resource_id == agent.id,
                    )
                )
            ).scalars().all()
            assert grants, "下架后授权记录必须保留，重新上架才能自动恢复"
    finally:
        await _cleanup(agent.id)


@pytest.mark.asyncio
async def test_publish_without_published_version_returns_400(db_session):
    """没有已发布版本的智能体，接口层直接 400，而不是悄悄设成系统级。"""
    admin_key = await AuthService.generate_api_key(f"sys_api_{_uid()}", role="admin", db=db_session)
    agent = await _make_agent(db_session, owner="api_owner")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-API-Key": admin_key}
            # 把唯一版本改成草稿
            from app.core.orm import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                version = (
                    await session.execute(
                        select(AIAgentVersion).where(AIAgentVersion.agent_id == agent.id)
                    )
                ).scalars().first()
                version.status = "DRAFT"
                await session.commit()

            resp = await client.post(
                f"/api/portal/agents/{agent.id}/system/publish",
                json={"reason": "试试"},
                headers=headers,
            )
        assert resp.status_code == 400
        assert "发布至少一个版本" in resp.json()["detail"]
    finally:
        await _cleanup(agent.id)


@pytest.mark.asyncio
async def test_create_agent_rejects_manual_system_flag(db_session):
    """新建时不能直接设系统级：必须走发布 / 审核，否则整条流程与留痕都能绕过。"""
    admin_key = await AuthService.generate_api_key(f"sys_create_{_uid()}", role="admin", db=db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/portal/agents/",
            json={
                "name": f"manual-sys-{_uid()}",
                "display_name": "想直接设系统级",
                "is_system": True,
                "is_enabled": True,
                "engine_type": "LOCAL",
                "engine_config": {"dataset_ids": []},
            },
            headers={"X-API-Key": admin_key},
        )
    assert resp.status_code == 400
    assert "不能通过新建或编辑设置" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_agent_rejects_flipping_is_system(db_session):
    """编辑时把普通智能体改成系统级同样要拦；回传当前值则不算修改。"""
    admin_key = await AuthService.generate_api_key(f"sys_update_{_uid()}", role="admin", db=db_session)
    agent = await _make_agent(db_session, owner="api_owner")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-API-Key": admin_key}
            base = {
                "name": agent.name,
                "display_name": agent.display_name,
                "description": agent.description,
                "is_enabled": True,
                "engine_type": "LOCAL",
                "engine_config": {"dataset_ids": []},
            }

            flipped = await client.put(
                f"/api/portal/agents/{agent.id}",
                json={**base, "is_system": True},
                headers=headers,
            )
            assert flipped.status_code == 400, flipped.text
            assert "不能通过编辑修改" in flipped.json()["detail"]

            # 回传当前值（False）不是「修改」，不能被误伤
            same = await client.put(
                f"/api/portal/agents/{agent.id}",
                json={**base, "is_system": False, "display_name": "改个名字"},
                headers=headers,
            )
            assert same.status_code == 200, same.text

        from app.core.orm import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            stored = await session.get(AIAgent, agent.id)
            assert stored.is_system is False, "被拒绝的请求不能留下半成品状态"
    finally:
        await _cleanup(agent.id)


@pytest.mark.asyncio
async def test_update_system_agent_allows_echoing_is_system_true(db_session):
    """管理员编辑系统级智能体时前端会回传 is_system=true，这不能算「修改」而被拦。"""
    admin_key = await AuthService.generate_api_key(f"sys_echo_{_uid()}", role="admin", db=db_session)
    agent = await _make_agent(db_session, owner="api_owner")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"X-API-Key": admin_key}
            published = await client.post(
                f"/api/portal/agents/{agent.id}/system/publish",
                json={"reason": "先上架"},
                headers=headers,
            )
            assert published.status_code == 200, published.text

            resp = await client.put(
                f"/api/portal/agents/{agent.id}",
                json={
                    "name": agent.name,
                    "display_name": "系统级改名",
                    "description": "管理员订正描述",
                    "is_enabled": True,
                    "is_system": True,
                    "engine_type": "LOCAL",
                    "engine_config": {"dataset_ids": []},
                },
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["is_system"] is True
    finally:
        await _cleanup(agent.id)
