import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_active_conversation_sync(db_session):
    """
    测试获取和设置云端活动会话 ID 的功能（全球/用户唯一级）
    """
    uid = str(uuid.uuid4())[:8]
    user_key = await AuthService.generate_api_key(f"test_active_user_{uid}", role="user", db=db_session)
    
    headers = {"X-API-Key": user_key}
    conv_1 = f"conv-{uuid.uuid4()}"
    conv_2 = f"conv-{uuid.uuid4()}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. 初始获取，应该没有值
        resp = await client.get(
            "/api/v1/chat/active",
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["data"]["conversation_id"] is None

        # 2. 设置活跃会话为 conv_1
        payload = {
            "conversation_id": conv_1
        }
        resp = await client.post(
            "/api/v1/chat/active",
            json=payload,
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["data"]["status"] == "success"

        # 3. 再次获取，应该返回 conv_1
        resp = await client.get(
            "/api/v1/chat/active",
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["conversation_id"] == conv_1

        # 4. 更新活跃会话为 conv_2
        payload = {
            "conversation_id": conv_2
        }
        resp = await client.post(
            "/api/v1/chat/active",
            json=payload,
            headers=headers
        )
        assert resp.status_code == 200

        # 5. 再次获取，应该返回新的 conv_2
        resp = await client.get(
            "/api/v1/chat/active",
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["conversation_id"] == conv_2


@pytest.mark.asyncio
async def test_get_conversation_history_route_contract(db_session):
    """测试获取会话历史接口正确匹配路由并返回 200（避免装饰器错位导致 400 Bad Request）"""
    uid = str(uuid.uuid4())[:8]
    user_key = await AuthService.generate_api_key(f"test_history_user_{uid}", role="user", db=db_session)
    headers = {"X-API-Key": user_key}
    conv_id = f"conv-{uuid.uuid4()}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/chat/conversation/{conv_id}",
            headers=headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 200
        assert data["data"]["conversation_id"] == conv_id
        assert isinstance(data["data"]["messages"], list)
