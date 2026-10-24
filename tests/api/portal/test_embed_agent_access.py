"""EmbedChat URL agent_id 深链访问校验：404 不存在 / 403 无权限。"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.models.agent import AIAgent
from app.services.auth_service import AuthService


def unique_id() -> str:
    return str(uuid.uuid4())[:8]


@pytest.mark.asyncio
async def test_embed_access_ok_by_name_for_admin(db_session):
    admin_key = await AuthService.generate_api_key(
        f"embed_admin_{unique_id()}", role="admin", db=db_session
    )
    agent_name = f"kb-{unique_id()}"
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=agent_name,
        display_name="知识库专家",
        description="test",
        is_system=True,
        is_enabled=True,
        engine_type="LOCAL",
        capabilities=["knowledge_base"],
        created_by="admin",
    )
    db_session.add(agent)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/portal/agents/{agent_name}/embed-access",
            headers={"X-API-Key": admin_key},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == agent.id
    assert data["name"] == agent_name
    assert "knowledge_base" in (data.get("capabilities") or [])


@pytest.mark.asyncio
async def test_embed_access_not_found(db_session):
    admin_key = await AuthService.generate_api_key(
        f"embed_admin_{unique_id()}", role="admin", db=db_session
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/agents/does-not-exist-agent/embed-access",
            headers={"X-API-Key": admin_key},
        )
    assert resp.status_code == 404
    body = resp.json()
    assert body["data"]["code"] == "AGENT_NOT_FOUND"
    assert "不存在" in body["message"]


@pytest.mark.asyncio
async def test_embed_access_forbidden_for_normal_user(db_session):
    admin_key = await AuthService.generate_api_key(
        f"embed_admin_{unique_id()}", role="admin", db=db_session
    )
    user_key = await AuthService.generate_api_key(
        f"embed_user_{unique_id()}", role="user", db=db_session
    )

    agent_name = f"sys-{unique_id()}"
    agent = AIAgent(
        id=str(uuid.uuid4()),
        name=agent_name,
        display_name="受限系统专家",
        description="test",
        is_system=True,
        is_enabled=True,
        engine_type="LOCAL",
        capabilities=["data_query"],
        created_by="admin",
    )
    db_session.add(agent)
    await db_session.commit()

    # Sanity: admin can access
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get(
            f"/api/portal/agents/{agent.id}/embed-access",
            headers={"X-API-Key": admin_key},
        )
        assert ok.status_code == 200

        denied = await client.get(
            f"/api/portal/agents/{agent.id}/embed-access",
            headers={"X-API-Key": user_key},
        )
    assert denied.status_code == 403
    body = denied.json()
    assert body["data"]["code"] == "AGENT_FORBIDDEN"
    assert "无权" in body["message"]
    # 403 不回显内部字段：回显 id/name/display_name 会把「名字 → UUID + 是否存在」
    # 做成公开查询，正好给按 id 操作的读接口供弹药
    for leaked in ("agent_id", "agent_name", "display_name"):
        assert leaked not in body["data"], f"403 不应回显 {leaked}"

    # Ensure agent still exists
    found = (
        await db_session.execute(select(AIAgent).where(AIAgent.id == agent.id))
    ).scalar_one_or_none()
    assert found is not None
