"""智能体分享接口：路由顺序、权限，以及「分享 → 收件箱 → 接受」的串通。

路由顺序是老问题：``/shares/received`` 里的 "shares" 会被 ``/{agent_id}/shares``
的路径参数吃掉，必须在它之前注册。之前 ``transfer-owner/candidates``
已经因为同样的原因 404 过一次。
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal, Base
from app.main import app
from app.models.agent import AIAgent, AIAgentVersion
from app.models.agent_share import AgentShareRequest
from app.models.portal_notification import PortalNotification
from app.services.ai.agent_share_service import AgentShareService
from app.services.auth_service import AuthService


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _ensure_share_table(sync_conn) -> None:
    """确保测试库里的分享表与模型定义一致。

    只做 ``create(checkfirst=True)`` 不够：表一旦存在就不会再改，
    模型加了列之后测试会报 ``Unknown column``。这里比对列名，不一致就重建。
    """
    from sqlalchemy import inspect

    table = Base.metadata.tables["ai_agent_share_requests"]
    inspector = inspect(sync_conn)
    if table.name in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        expected = {col.name for col in table.columns}
        if not expected.issubset(existing):
            table.drop(sync_conn, checkfirst=True)
    table.create(sync_conn, checkfirst=True)


@pytest.fixture(autouse=True)
async def share_table():
    """V186 的表由测试自己建：本地库可能还没执行迁移。"""
    async with AsyncSessionLocal() as session:
        conn = await session.connection()
        await conn.run_sync(_ensure_share_table)
        await session.commit()
    AgentShareService.reset_probe_state_for_tests(None)
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AgentShareRequest))
        await session.commit()
    AgentShareService.reset_probe_state_for_tests(None)


async def _make_agent(session, *, owner: str, is_system: bool = False) -> AIAgent:
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=f"api-share-{_uid()}",
        display_name="分享接口测试智能体",
        created_by=owner,
        is_system=is_system,
        is_enabled=True,
        engine_type="LOCAL",
        onboarding_step="COMPLETE",
    )
    session.add(agent)
    await session.flush()
    session.add(
        AIAgentVersion(
            id=str(uuid.uuid4()),
            agent_id=agent.id,
            version_number=1,
            system_prompt="你是分享测试助手",
            model_name="deepseek-chat",
            tools=[],
            status="PUBLISHED",
        )
    )
    await session.flush()
    await session.commit()
    return agent


async def _cleanup(*agent_ids: str) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AgentShareRequest).where(AgentShareRequest.agent_id.in_(list(agent_ids)))
        )
        await session.execute(
            delete(PortalNotification).where(
                PortalNotification.resource_type == "agent",
                PortalNotification.resource_id.in_(list(agent_ids)),
            )
        )
        await session.execute(
            delete(AIAgentVersion).where(AIAgentVersion.agent_id.in_(list(agent_ids)))
        )
        await session.execute(delete(AIAgent).where(AIAgent.id.in_(list(agent_ids))))
        await session.commit()


@pytest.mark.asyncio
async def test_received_route_is_not_shadowed(db_session):
    """``/shares/received`` 不能被 ``/{agent_id}/shares`` 抢走。"""
    key = await AuthService.generate_api_key(f"share_api_{_uid()}", role="user", db=db_session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/agents/shares/received", headers={"X-API-Key": key}
        )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_system_agent_share_is_rejected(db_session):
    """系统级智能体即使有 admin 身份也走不通分享（除非它本来就不是系统级）。"""
    admin_key = await AuthService.generate_api_key(
        f"share_admin_{_uid()}", role="admin", db=db_session
    )
    agent = await _make_agent(db_session, owner="admin", is_system=True)

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/portal/agents/{agent.id}/share",
                json={"recipient_user_names": ["someone"], "message": "x"},
                headers={"X-API-Key": admin_key},
            )
        assert resp.status_code == 409
        assert "系统级" in resp.json()["detail"]
    finally:
        await _cleanup(agent.id)


@pytest.mark.asyncio
async def test_share_candidates_open_to_normal_users(db_session):
    """分享候选用户只要求登录。

    不能复用转交候选（要求 ``element:agent:system_publish``）：分享是普通用户的
    核心能力，卡在管理员权限上会让「分享给同事」永远显示空列表。
    """
    key = await AuthService.generate_api_key(
        f"share_cand_{_uid()}", role="user", db=db_session
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/agents/shares/candidates", headers={"X-API-Key": key}
        )
        # 转交候选仍然只对管理员开放
        transfer = await client.get(
            "/api/portal/agents/transfer-owner/candidates", headers={"X-API-Key": key}
        )

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert transfer.status_code == 403


@pytest.mark.asyncio
async def test_sent_and_count_routes(db_session):
    """``/shares/sent`` 与 ``/shares/counts`` 都不能被 ``/{agent_id}/shares`` 抢走。"""
    key = await AuthService.generate_api_key(
        f"share_sent_{_uid()}", role="user", db=db_session
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sent = await client.get(
            "/api/portal/agents/shares/sent", headers={"X-API-Key": key}
        )
        counts = await client.get(
            "/api/portal/agents/shares/counts", headers={"X-API-Key": key}
        )

    assert sent.status_code == 200 and isinstance(sent.json(), list)
    assert counts.status_code == 200
    body = counts.json()
    assert set(body) == {"received_pending", "sent_pending", "sent_total"}
